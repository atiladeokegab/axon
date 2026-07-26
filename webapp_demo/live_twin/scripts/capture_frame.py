"""
Grabs one raw frame from the configured camera and saves it — used to
capture the 5 pad-placement calibration photos one at a time.

Usage:
    uv run python scripts/capture_frame.py <output_path>
"""
import pathlib
import sys

import cv2

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from live_twin.backend import config


def main(out_path: str):
    source = int(config.VIDEO_SOURCE) if config.VIDEO_SOURCE.isdigit() else config.VIDEO_SOURCE
    cap = cv2.VideoCapture(source)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        print("failed to capture a frame")
        sys.exit(1)
    cv2.imwrite(out_path, frame)
    print(f"saved {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
