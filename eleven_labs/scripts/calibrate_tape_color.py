"""
Suggests MARKER_HSV_LOWER/MARKER_HSV_UPPER from a real photo of the tape
you're using, instead of the blind pink/magenta placeholder in config.py.

Usage:
    uv run python scripts/calibrate_tape_color.py <image_path> <x> <y>

<x> <y> are pixel coordinates of a point on one tape dot in the photo — open
the image in any viewer, hover over the dot, read off the pixel coords it
shows. Prints the two env vars to export, and writes <image_path>_mask.png
so you can eyeball whether the suggested range actually isolates the dots
(and only the dots) before trusting it.
"""
import pathlib
import sys

import cv2

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from backend.placement.hsv_calibration import suggest_hsv_range, DEFAULT_TOLERANCE


def main(image_path: str, x: int, y: int, tolerance: tuple = DEFAULT_TOLERANCE):
    image = cv2.imread(image_path)
    if image is None:
        print(f"failed to read image: {image_path}")
        sys.exit(1)

    lower, upper = suggest_hsv_range(image, x, y, tolerance=tolerance)

    print(f"MARKER_HSV_LOWER={lower[0]},{lower[1]},{lower[2]}")
    print(f"MARKER_HSV_UPPER={upper[0]},{upper[1]},{upper[2]}")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    mask_path = f"{image_path}_mask.png"
    cv2.imwrite(mask_path, mask)
    print(f"wrote {mask_path} - white should cover only the tape dots, nothing else. "
          f"If it's too noisy, retry with a tighter tolerance.")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
