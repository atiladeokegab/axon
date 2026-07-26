"""
Covers the threaded capture that decouples the camera from inference.

The property that matters: a consumer always gets the *newest* frame and never
a queued backlog. Inline capture previously meant cap.read() (~33ms at 30fps)
and MediaPipe (~13ms) ran back to back, so the loop missed every second frame
and quantised to ~15Hz — under the control loop's 20Hz floor.
"""
import threading
import time

import pytest

from live_twin.backend.pose.broadcaster import LatestFrameCamera


class _FakeCapture:
    """Stands in for cv2.VideoCapture, emitting numbered frames at a set rate."""

    def __init__(self, interval=0.005, fail_after=None, opened=True):
        self.interval = interval
        self.fail_after = fail_after
        self._opened = opened
        self.frames_read = 0
        self.released = False

    def isOpened(self):
        return self._opened

    def getBackendName(self):
        return "fake"

    def read(self):
        if self.fail_after is not None and self.frames_read >= self.fail_after:
            return False, None
        time.sleep(self.interval)
        self.frames_read += 1
        return True, f"frame-{self.frames_read}"

    def release(self):
        self.released = True
        self._opened = False


@pytest.fixture
def camera():
    created = []

    def factory():
        cap = _FakeCapture()
        created.append(cap)
        return cap

    cam = LatestFrameCamera(source_factory=factory)
    cam.created = created
    yield cam
    cam.close()


def _wait_for_frame(cam, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cam.latest() is not None:
            return cam.latest()
        time.sleep(0.005)
    raise AssertionError("camera produced no frame within timeout")


def test_returns_nothing_before_the_first_frame_arrives(camera):
    assert camera.latest() is None


def test_captures_frames_once_started(camera):
    camera.start()

    frame, sequence = _wait_for_frame(camera)

    assert frame.startswith("frame-")
    assert sequence >= 1


def test_a_slow_consumer_gets_the_newest_frame_not_a_backlog(camera):
    """
    The whole point: falling behind must cost you intermediate frames, never
    hand you stale ones. A queue here would let the pose drift behind reality.
    """
    camera.start()
    _wait_for_frame(camera)

    _, first_sequence = camera.latest()
    time.sleep(0.2)  # consumer "busy" while many frames are produced
    frame, later_sequence = camera.latest()

    assert later_sequence > first_sequence + 1, "expected frames to have been skipped"
    # The frame handed over is the one matching the newest sequence, not an older one.
    assert frame == f"frame-{later_sequence}"


def test_sequence_increases_monotonically(camera):
    camera.start()
    _wait_for_frame(camera)

    seen = []
    for _ in range(5):
        seen.append(camera.latest()[1])
        time.sleep(0.02)

    assert seen == sorted(seen)


def test_reopens_the_camera_after_it_stops_delivering(camera):
    """A camera unplugged/reclaimed mid-session must recover, not wedge."""
    failing = _FakeCapture(fail_after=3)
    healthy = _FakeCapture()
    sources = [failing, healthy]
    cam = LatestFrameCamera(source_factory=lambda: sources.pop(0) if sources else _FakeCapture())

    try:
        cam.start()
        _wait_for_frame(cam)
        # Wait for the *recovery*, not merely the failure: reopening is deferred
        # by a retry backoff, so asserting as soon as the bad capture is released
        # would check before the replacement has had a chance to read anything.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and healthy.frames_read == 0:
            time.sleep(0.01)

        assert failing.released is True, "failing capture was never released"
        assert healthy.frames_read > 0, "did not fail over to a working capture"
    finally:
        cam.close()


def test_keeps_retrying_when_the_camera_will_not_open():
    """Startup with no camera must not crash or spin the CPU."""
    attempts = []

    def factory():
        attempts.append(1)
        return _FakeCapture(opened=False)

    cam = LatestFrameCamera(source_factory=factory)
    try:
        cam.start()
        time.sleep(0.3)
        assert cam.latest() is None
        assert attempts, "never attempted to open"
        # Retries are spaced (0.5s), not a hot loop.
        assert len(attempts) < 10
    finally:
        cam.close()


def test_close_stops_the_thread_and_releases_the_camera(camera):
    camera.start()
    _wait_for_frame(camera)

    camera.close()

    assert camera._thread is None
    assert all(cap.released for cap in camera.created), "capture not released"
    names = [t.name for t in threading.enumerate()]
    assert "camera-capture" not in names


def test_start_is_idempotent(camera):
    camera.start()
    first = camera._thread
    camera.start()

    assert camera._thread is first, "second start() spawned a duplicate capture thread"


def test_close_without_start_is_safe():
    LatestFrameCamera(source_factory=_FakeCapture).close()
