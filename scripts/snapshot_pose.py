"""
Grabs one webcam frame, runs the real pose pipeline on it, draws the
detected shoulder/elbow/wrist landmarks, and saves both the raw and
annotated frames to disk. No GUI window needed — good for a quick check
without babysitting a live cv2.imshow loop.
"""
import pathlib
import sys

import cv2

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from backend.pose.estimator import ArmPoseEstimator
from backend import config


def main(out_prefix: str = "scratch"):
    cap = cv2.VideoCapture(int(config.VIDEO_SOURCE) if config.VIDEO_SOURCE.isdigit() else config.VIDEO_SOURCE)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        print("failed to capture a frame")
        sys.exit(1)

    cv2.imwrite(f"{out_prefix}_raw_frame.png", frame)

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    est = ArmPoseEstimator()
    landmarks = est.process(rgb)
    est.close()

    print("landmarks:", landmarks)

    if landmarks:
        h, w = frame.shape[:2]
        points = {name: (int(x * w), int(y * h)) for name, (x, y) in landmarks.items()}
        for name, pt in points.items():
            cv2.circle(frame, pt, 8, (0, 255, 0), -1)
            cv2.putText(frame, name, (pt[0] + 10, pt[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.line(frame, points["shoulder"], points["elbow"], (0, 255, 255), 2)
        cv2.line(frame, points["elbow"], points["wrist"], (0, 255, 255), 2)

    cv2.imwrite(f"{out_prefix}_annotated_frame.png", frame)
    print(f"saved {out_prefix}_raw_frame.png and {out_prefix}_annotated_frame.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "scratch")
