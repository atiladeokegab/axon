"""One-off: downloads the MediaPipe pose landmarker model into models/."""
import pathlib
import urllib.request

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)
DEST = pathlib.Path(__file__).resolve().parent.parent / "models" / "pose_landmarker_lite.task"

if __name__ == "__main__":
    DEST.parent.mkdir(exist_ok=True)
    print(f"downloading {MODEL_URL} -> {DEST}")
    urllib.request.urlretrieve(MODEL_URL, DEST)
    print("done:", DEST.stat().st_size, "bytes")
