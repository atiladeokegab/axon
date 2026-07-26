from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidImageError(ValueError):
    """Raised when uploaded bytes cannot be decoded as an image."""


def normalize_image_orientation(image_bytes: bytes) -> bytes:
    """
    Apply EXIF orientation once and remove metadata.

    The returned JPEG is the single source of pixels for YOLO, OpenAI, and
    OpenCV, preventing the services from interpreting orientation differently.
    """
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            oriented = ImageOps.exif_transpose(source)
            rgb_image = oriented.convert("RGB")
            output = BytesIO()
            rgb_image.save(output, format="JPEG", quality=95)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            "One or both uploads could not be decoded as a valid image."
        ) from exc
