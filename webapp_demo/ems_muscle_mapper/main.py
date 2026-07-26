import logging
import base64
import hashlib
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)

# Site-wide file, then the app-specific one. Both are loaded in every module
# that needs settings, in the same order, so a key works wherever it is put -
# see the note in services/vlm_analyzer.py for the bug this prevents.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from ems_muscle_mapper.services.arm_region import ArmNotFoundError, extract_arm_region
from ems_muscle_mapper.services.vlm_analyzer import (
    APIConfigurationError,
    UnsupportedImageError,
    analyze_muscle_movement,
    refine_muscle_movement,
)
from ems_muscle_mapper.services.image_processor import build_alt_text, draw_ems_ui
from ems_muscle_mapper.services.image_normalizer import InvalidImageError, normalize_image_orientation
from ems_muscle_mapper.schemas import AccuracyFeedback, MuscleAnalysisResult
from live_twin.backend.main import app as live_twin_app
from live_twin.backend.main import runtime as live_twin_runtime
# Imported AFTER live_twin: the Human Control service takes the LiveRuntime
# instance from it, so that module must have been created first.
from human_control.app import app as human_control_app
from human_control.app import service as human_control_service

logger = logging.getLogger(__name__)
SITE_ROOT = Path(__file__).resolve().parent.parent
HERO_IMAGE = SITE_ROOT / "hero.jpeg"
SITE_STATIC_DIR = SITE_ROOT / "static"
SITE_TEMPLATE_DIR = SITE_ROOT / "templates"
MAPPER_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
mapper_app = FastAPI(title="EMS Muscle Mapper")
mapper_app.mount("/static", StaticFiles(directory=MAPPER_TEMPLATE_DIR), name="static")
mapper_app.mount(
    "/shared-static",
    StaticFiles(directory=SITE_STATIC_DIR),
    name="shared-static",
)
templates = Jinja2Templates(directory=[MAPPER_TEMPLATE_DIR, SITE_TEMPLATE_DIR])
site_templates = Jinja2Templates(directory=[SITE_ROOT, SITE_TEMPLATE_DIR])

_CACHE_MAX_PAIRS = 32
_result_cache: OrderedDict[str, dict[str, object]] = OrderedDict()
_result_cache_lock = Lock()


def _image_pair_cache_key(lax_bytes: bytes, flexed_bytes: bytes) -> str:
    """Hash the ordered normalized image pair without retaining the uploads."""
    digest = hashlib.sha256()
    for image_bytes in (lax_bytes, flexed_bytes):
        digest.update(len(image_bytes).to_bytes(8, "big"))
        digest.update(image_bytes)
    return digest.hexdigest()


def _clear_result_cache() -> None:
    """Clear cached results (primarily for tests and controlled reloads)."""
    with _result_cache_lock:
        _result_cache.clear()


def _build_result(
    processed_image: bytes,
    analysis: MuscleAnalysisResult,
    analysis_id: str,
) -> dict[str, object]:
    return {
        "image_base64": base64.b64encode(processed_image).decode("ascii"),
        "alt_text": build_alt_text(analysis),
        "analysis": analysis.model_dump(mode="json"),
        "analysis_id": analysis_id,
    }


@mapper_app.get("/")
async def home(request: Request):
    """Render the frontend and its reusable HTML partials."""
    return templates.TemplateResponse(request=request, name="index.html")

@mapper_app.post("/analyze")
async def process_images(lax_image: UploadFile = File(...), flexed_image: UploadFile = File(...)):
    """Receives the two images, processes them, and returns an annotated image."""
    lax_bytes = await lax_image.read()
    flexed_bytes = await flexed_image.read()

    if not lax_bytes or not flexed_bytes:
        raise HTTPException(status_code=400, detail="Both uploaded images must contain data.")
    
    try:
        # 1. Apply EXIF orientation and strip metadata once. Every downstream
        # service now sees the exact same upright pixels.
        normalized_lax = normalize_image_orientation(lax_bytes)
        normalized_flexed = normalize_image_orientation(flexed_bytes)
        cache_key = _image_pair_cache_key(normalized_lax, normalized_flexed)

        # Keep lookup and generation together so simultaneous identical uploads
        # cannot produce two different results before the first is cached.
        with _result_cache_lock:
            cached_result = _result_cache.get(cache_key)
            if cached_result is not None:
                _result_cache.move_to_end(cache_key)
                return dict(cached_result)

            # 2. Use the flexed image to choose an arm, then require the same
            # anatomical side in the relaxed image.
            flexed_region = extract_arm_region(normalized_flexed)
            lax_region = extract_arm_region(
                normalized_lax, preferred_side=flexed_region.side
            )

            # 3. Ask the VLM about the compact arm crops, then map its
            # crop-relative coordinates back to the full normalized image.
            crop_analysis = analyze_muscle_movement(
                lax_region.image_bytes,
                flexed_region.image_bytes,
                arm_side=flexed_region.side,
                pose_context=flexed_region.pose_prompt_context(),
            )
            refined_crop_analysis = flexed_region.refine_crop_analysis(crop_analysis)
            analysis_result = flexed_region.map_analysis_to_source(
                refined_crop_analysis
            )

            # 4. Render onto the same oriented pixels used by YOLO and OpenAI.
            processed_image = draw_ems_ui(normalized_flexed, analysis_result)
            result = _build_result(processed_image, analysis_result, cache_key)
            _result_cache[cache_key] = result
            _result_cache.move_to_end(cache_key)
            while len(_result_cache) > _CACHE_MAX_PAIRS:
                _result_cache.popitem(last=False)
            return dict(result)
        
    except (InvalidImageError, ArmNotFoundError, UnsupportedImageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except APIConfigurationError as exc:
        logger.error("OpenAI configuration error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthenticationError as exc:
        logger.warning("OpenAI rejected the configured API credentials.")
        raise HTTPException(
            status_code=503,
            detail="The server's OpenAI API key was rejected. Replace OPENAI_API_KEY and restart the server.",
        ) from exc
    except PermissionDeniedError as exc:
        logger.warning("OpenAI denied model or project access.")
        raise HTTPException(
            status_code=503,
            detail="The configured OpenAI project does not have access to this model.",
        ) from exc
    except RateLimitError as exc:
        logger.warning("OpenAI rate or quota limit reached.")
        raise HTTPException(
            status_code=429,
            detail="The OpenAI rate or quota limit was reached. Check project billing or retry shortly.",
        ) from exc
    except APITimeoutError as exc:
        logger.warning("OpenAI request timed out.")
        raise HTTPException(
            status_code=504,
            detail="The OpenAI request timed out. Please try again.",
        ) from exc
    except APIConnectionError as exc:
        logger.warning("Could not connect to OpenAI: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not connect to OpenAI. Check the server network and OPENAI_BASE_URL.",
        ) from exc
    except APIStatusError as exc:
        logger.warning("OpenAI returned HTTP %s.", exc.status_code)
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI returned an upstream HTTP {exc.status_code} error.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected analysis failure.")
        raise HTTPException(
            status_code=500,
            detail="Analysis failed unexpectedly. Check the server log for details.",
        ) from exc


@mapper_app.post("/feedback", status_code=202)
async def record_accuracy_feedback(feedback: AccuracyFeedback):
    """Record whether the user accepted the current mapping."""
    logger.info(
        "Mapping accuracy feedback: analysis_id=%s accurate=%s",
        feedback.analysis_id[:12],
        feedback.accurate,
    )
    return {"status": "received"}


@mapper_app.post("/refine")
async def refine_images(
    lax_image: UploadFile = File(...),
    flexed_image: UploadFile = File(...),
    analysis_json: str = Form(...),
    analysis_id: str = Form(...),
    feedback: str = Form(..., min_length=3, max_length=1000),
):
    """Revise a returned mapping from the user's specific visual correction."""
    lax_bytes = await lax_image.read()
    flexed_bytes = await flexed_image.read()
    feedback = feedback.strip()

    if not lax_bytes or not flexed_bytes:
        raise HTTPException(
            status_code=400,
            detail="Both original images are required to refine the mapping.",
        )
    if len(analysis_json) > 50_000:
        raise HTTPException(status_code=400, detail="The current mapping is too large.")
    if not feedback:
        raise HTTPException(
            status_code=400,
            detail="Describe what should be corrected before refining.",
        )

    try:
        current_analysis = MuscleAnalysisResult.model_validate_json(analysis_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="The current mapping could not be validated.",
        ) from exc

    try:
        normalized_lax = normalize_image_orientation(lax_bytes)
        normalized_flexed = normalize_image_orientation(flexed_bytes)
        expected_id = _image_pair_cache_key(normalized_lax, normalized_flexed)
        if analysis_id != expected_id:
            raise HTTPException(
                status_code=400,
                detail="The correction does not match the uploaded image pair.",
            )

        flexed_region = extract_arm_region(normalized_flexed)
        lax_region = extract_arm_region(
            normalized_lax, preferred_side=flexed_region.side
        )
        crop_analysis = flexed_region.map_analysis_to_crop(current_analysis)
        revised_crop_analysis = refine_muscle_movement(
            lax_region.image_bytes,
            flexed_region.image_bytes,
            crop_analysis,
            feedback,
            arm_side=flexed_region.side,
            pose_context=flexed_region.pose_prompt_context(),
        )
        revised_crop_analysis = flexed_region.refine_crop_analysis(
            revised_crop_analysis
        )
        revised_analysis = flexed_region.map_analysis_to_source(
            revised_crop_analysis
        )
        processed_image = draw_ems_ui(normalized_flexed, revised_analysis)
        result = _build_result(processed_image, revised_analysis, expected_id)

        with _result_cache_lock:
            _result_cache[expected_id] = result
            _result_cache.move_to_end(expected_id)
        logger.info(
            "Mapping refined from user feedback: analysis_id=%s",
            expected_id[:12],
        )
        return result

    except HTTPException:
        raise
    except (InvalidImageError, ArmNotFoundError, UnsupportedImageError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except APIConfigurationError as exc:
        logger.error("OpenAI configuration error during refinement: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=503,
            detail="The server's OpenAI API key was rejected.",
        ) from exc
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=503,
            detail="The configured OpenAI project cannot access this model.",
        ) from exc
    except RateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail="The OpenAI rate or quota limit was reached. Retry shortly.",
        ) from exc
    except APITimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="The refinement request timed out. Please try again.",
        ) from exc
    except APIConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not connect to OpenAI. Check the server network.",
        ) from exc
    except APIStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI returned an upstream HTTP {exc.status_code} error.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected refinement failure.")
        raise HTTPException(
            status_code=500,
            detail="Refinement failed unexpectedly. Check the server log.",
        ) from exc


@asynccontextmanager
async def _site_lifespan(_app: FastAPI):
    try:
        yield
    finally:
        # Stop the control loop BEFORE tearing down the camera it reads from,
        # and before the runtime shuts down, so the last thing the board hears
        # is an explicit disarm rather than silence. Silence would work - the
        # watchdog opens every relay - but it would drop the arm rather than
        # lower it, and a shutdown path should not rely on a safety backstop.
        await human_control_service.stop()
        await live_twin_runtime.shutdown()


app = FastAPI(title="Axon", lifespan=_site_lifespan)
app.mount("/static", StaticFiles(directory=SITE_STATIC_DIR), name="site-static")


@app.get("/")
async def landing(request: Request):
    """Render the parent site's landing page."""
    return site_templates.TemplateResponse(request=request, name="index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Browsers request /favicon.ico regardless of the <link> tags.

    Without this every page load logs a 404 for a file nobody asked us to make.
    The PNG is served directly - browsers have accepted PNG at this path for
    years, and it saves shipping a second copy of the same artwork.
    """
    icon = SITE_STATIC_DIR / "images" / "icon.png"
    if icon.is_file():
        return FileResponse(icon, media_type="image/png")
    raise HTTPException(status_code=404, detail="No icon installed.")


@app.get("/hero.jpeg")
async def hero_image():
    """Serve the landing page's root-level hero image.

    FALLS BACK RATHER THAN RAISING. FileResponse stats the file lazily, inside
    the response cycle, so a missing image escaped as a RuntimeError and a full
    ASGI traceback - three times per page load, because the landing page uses
    the same image for the base layer and two distortion layers. A decorative
    image is not worth a 500, and the noise buries real errors in the log.

    The file is absent from fresh checkouts because .gitignore excludes
    "*.jpeg", so it was never committed. Rather than fail, fall back to an image
    that IS tracked; a slightly different hero beats a broken page.
    """
    for candidate in (HERO_IMAGE, SITE_STATIC_DIR / "images" / "hand-hero.png"):
        if candidate.is_file():
            return FileResponse(candidate)
    # Nothing to serve. 404 is the honest answer and the browser just shows no
    # image, rather than the server reporting itself as broken.
    raise HTTPException(status_code=404, detail="Hero image is not installed.")


app.mount("/ems-muscle-mapper", mapper_app, name="ems-muscle-mapper")
app.mount("/live-twin", live_twin_app, name="live-twin")
app.mount("/human-control", human_control_app, name="human-control")
