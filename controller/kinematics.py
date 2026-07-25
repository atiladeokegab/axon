"""Joint-angle extraction from 3D joint positions.

We model the arm as a robotic manipulator with 3 controlled DOF:
    shoulder_flex  - arm forward/up      (anterior/posterior deltoid)
    shoulder_abd   - arm out to the side (middle deltoid; gravity adducts)
    elbow          - elbow flexion       (biceps / triceps)

The teammate's pose service supplies 3D positions for shoulder, elbow and
wrist. We derive angles here rather than trusting any angles it might send, so
the control loop owns its own definition of "where the arm is".

Frame convention (right-handed, subject-centred):
    +X = subject's forward,  +Y = subject's left,  +Z = up
If the pose service uses a different frame, convert at the ingest boundary
(pose_api.py) rather than here.
"""

import math


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    n = _norm(v)
    if n < 1e-9:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def angle_between(a, b):
    """Angle in degrees between two vectors."""
    na, nb = _norm(a), _norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    c = _dot(a, b) / (na * nb)
    c = max(-1.0, min(1.0, c))
    return math.degrees(math.acos(c))


def elbow_angle(shoulder, elbow, wrist):
    """Elbow FLEXION in degrees. 0 = fully straight arm, larger = more bent."""
    upper = _sub(shoulder, elbow)     # elbow -> shoulder
    fore = _sub(wrist, elbow)         # elbow -> wrist
    interior = angle_between(upper, fore)
    return 180.0 - interior


def shoulder_angles(shoulder, elbow):
    """Return (flexion_deg, abduction_deg) for the upper-arm segment.

    flexion   = rotation of the upper arm forward from hanging straight down,
                measured in the sagittal plane. Range -180..180.
    abduction = rotation of the upper arm out sideways from hanging down,
                measured out of the sagittal plane. Range 0..90.

    Both are measured from the 'arm hanging at the side' rest pose, which is
    the natural zero for a seated subject and matches how gravity returns the
    limb when we drop the duty.

    DECOMPOSITION - and why the obvious version is wrong:

    An earlier implementation used atan2(forward, max(down, 1e-9)) for both.
    Clamping the denominator to a positive epsilon meant that the moment the
    arm passed horizontal (down <= 0) the result SATURATED at exactly 90 deg:
    100, 110 and 130 deg all measured 90.00. That is dangerous here, not merely
    inaccurate - JOINT_LIMITS allows flexion to 110 deg, so a reachable target
    became unmeasurable, the error could never close, and the integrator would
    wind up to DUTY_MAX and hold a muscle there indefinitely.

    Correct decomposition, treating the shoulder as a 2-DOF spherical joint:
      * abduction is the elevation OUT of the sagittal plane -> asin(lateral),
        which is naturally independent of how far forward the arm is;
      * flexion is the angle within the sagittal plane -> atan2 over the
        remaining forward/down components, with NO clamp, so it stays
        continuous through and past horizontal.
    """
    v = _unit(_sub(elbow, shoulder))   # shoulder -> elbow, points down at rest
    forward, lateral, vertical = v[0], v[1], v[2]
    down = -vertical                   # +1 hanging down, -1 straight up

    # Abduction: how far the arm has left the sagittal plane. asin is exact
    # here and does not depend on the flexion angle, so the two axes do not
    # cross-couple.
    lateral = max(-1.0, min(1.0, lateral))
    abduction = math.degrees(math.asin(abs(lateral)))

    # Flexion: angle in the sagittal plane. atan2 handles a negative
    # denominator correctly, giving the full range instead of saturating.
    sagittal = math.sqrt(max(0.0, 1.0 - lateral * lateral))
    if sagittal < 1e-9:
        flexion = 0.0                  # arm straight out sideways: undefined
    else:
        flexion = math.degrees(math.atan2(forward, down))
    return flexion, abduction


def joints_from_pose(shoulder, elbow, wrist):
    """Full joint vector from three 3D landmarks."""
    flex, abd = shoulder_angles(shoulder, elbow)
    return {
        "elbow": elbow_angle(shoulder, elbow, wrist),
        "shoulder_flex": flex,
        "shoulder_abd": abd,
    }


def forward_kinematics(joints, upper_len, fore_len):
    """Approximate hand position from joint angles.

    NOT USED BY THE CONTROL PATH. The controller is joint-space: the teleop keys
    jog joint targets directly and each joint runs its own independent PI loop.
    There is no Cartesian/end-effector control and no Jacobian anywhere.

    Kept as scaffolding for a future Cartesian jog mode or a 3D display. If you
    do build Cartesian control on top of this, you will need a damped
    least-squares Jacobian inverse - a naive inverse diverges as the arm
    straightens and the Jacobian loses rank.

    Deliberately simple: not a precision model of the shoulder complex.
    """
    flex = math.radians(joints.get("shoulder_flex", 0.0))
    abd = math.radians(joints.get("shoulder_abd", 0.0))
    elb = math.radians(joints.get("elbow", 0.0))

    # Upper arm direction from the rest (hanging) pose.
    ux = math.sin(flex) * math.cos(abd)
    uy = math.sin(abd)
    uz = -math.cos(flex) * math.cos(abd)

    ex = ux * upper_len
    ey = uy * upper_len
    ez = uz * upper_len

    # Forearm: elbow flexion lifts the hand toward the shoulder in the plane
    # defined by the upper arm and 'forward'.
    lift = math.sin(elb)
    along = math.cos(elb)
    hx = ex + (ux * along + lift) * fore_len
    hy = ey + uy * along * fore_len
    hz = ez + uz * along * fore_len
    return (hx, hy, hz)


def clamp_joint(name, value, limits):
    lo, hi = limits[name]
    return max(lo, min(hi, value))
