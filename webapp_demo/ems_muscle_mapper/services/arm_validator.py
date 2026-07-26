import cv2
import numpy as np
from ultralytics import YOLO


class _LazyPoseModel:
    """Defer the heavy YOLO load/download until an analysis actually needs it."""

    def __init__(self):
        self._model = None

    def __call__(self, *args, **kwargs):
        if self._model is None:
            # Ultralytics downloads this lightweight model on first analysis.
            self._model = YOLO("yolov8n-pose.pt")
        return self._model(*args, **kwargs)


pose_model = _LazyPoseModel()

def verify_arm_presence(image_bytes: bytes) -> bool:
    """Verifies an arm is visible in the image using YOLO Pose."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    # Run inference (verbose=False to keep server console clean)
    results = pose_model(img, verbose=False)
    
    # If no people are detected or no keypoints are found, reject
    if len(results) == 0 or results[0].keypoints is None:
        return False
        
    # Extract keypoints data
    # Shape is [num_people, 17_joints, 3_values(x, y, confidence)]
    keypoints_data = results[0].keypoints.data.cpu().numpy()
    
    if len(keypoints_data) == 0:
        return False
        
    # COCO Keypoint Indices:
    # 7: Left Elbow, 9: Left Wrist
    # 8: Right Elbow, 10: Right Wrist
    
    for person_kpts in keypoints_data:
        # Check left arm confidence scores
        left_elbow_conf = person_kpts[7][2]
        left_wrist_conf = person_kpts[9][2]
        left_visible = (left_elbow_conf > 0.7) and (left_wrist_conf > 0.7)
        
        # Check right arm confidence scores
        right_elbow_conf = person_kpts[8][2]
        right_wrist_conf = person_kpts[10][2]
        right_visible = (right_elbow_conf > 0.7) and (right_wrist_conf > 0.7)
        
        # If either arm is clearly visible, the image passes validation
        if left_visible or right_visible:
            return True
            
    return False
