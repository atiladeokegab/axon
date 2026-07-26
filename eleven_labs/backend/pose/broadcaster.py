"""
Single camera + pose-estimation producer shared across all WebSocket
clients, instead of main.py opening a new cv2.VideoCapture + PoseLandmarker
per connection (wasteful, and multiple clients would fight over the same
camera device).

One background task reads frames and pushes the latest pose and, when a
preview client is connected, a JPEG made from that exact same capture frame
onto bounded per-subscriber queues. Subscribers that fall behind drop old
items (maxsize=1) rather than building unbounded backlog.
"""
import asyncio
import logging
import threading
import time

import cv2

from backend import config
from backend.pose.control_link import PoseControlLink
from backend.pose.estimator import ArmPoseEstimator, validate_side

logger = logging.getLogger("pose_broadcaster")

TARGET_FPS = 20
FRAME_INTERVAL_S = 1 / TARGET_FPS


def _video_source():
    src = config.VIDEO_SOURCE
    return int(src) if src.isdigit() else src


class LatestFrameCamera:
    """
    Reads the camera on its own thread, keeping only the newest frame.

    Capture used to be inline: cap.read() blocks ~33ms waiting on the next frame
    of a 30fps camera, and MediaPipe then ran for ~13ms *afterwards*. Because the
    ~46ms total exceeded the camera's 33ms frame period, the loop missed every
    second frame and quantised to two periods — ~15Hz out of a 30fps camera,
    which is what put us under the control loop's 20Hz floor (POSE_API.md).
    Capturing independently lets inference spend its own budget without the
    camera waiting on it.

    Only this thread touches the VideoCapture: cv2 capture objects are not
    thread-safe. Consumers get a snapshot via latest().
    """

    def __init__(self, source_factory=None):
        self._source_factory = source_factory or (lambda: cv2.VideoCapture(_video_source()))
        self._cap = None
        self._frame = None
        # Monotonic counter, not a timestamp: it is how a consumer tells "a new
        # frame arrived" from "same frame again", without comparing pixels.
        self._sequence = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="camera-capture", daemon=True)
        self._thread.start()

    def _open(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = self._source_factory()
        if self._cap is not None and self._cap.isOpened():
            logger.info(
                "camera source %r opened via %s",
                config.VIDEO_SOURCE,
                self._cap.getBackendName(),
            )
        else:
            logger.warning(
                "camera source %r did not open — retrying until it becomes available",
                config.VIDEO_SOURCE,
            )

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                if self._cap is None or not self._cap.isOpened():
                    self._open()
                    if self._cap is None or not self._cap.isOpened():
                        self._stop.wait(0.5)
                    continue

                ok, frame = self._cap.read()
                if not ok:
                    logger.warning(
                        "camera source %r stopped delivering frames — reopening",
                        config.VIDEO_SOURCE,
                    )
                    self._cap.release()
                    self._cap = None
                    self._stop.wait(0.1)
                    continue

                with self._lock:
                    # Overwrite rather than queue: a backlog of frames would only
                    # let the consumer fall further behind reality.
                    self._frame = frame
                    self._sequence += 1
        finally:
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    def latest(self):
        """Newest frame as (frame, sequence), or None before the first arrives."""
        with self._lock:
            if self._frame is None:
                return None
            return self._frame, self._sequence

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Bounded: a wedged driver must not hang application shutdown. The
            # thread is a daemon, so a timeout here still lets the process exit.
            self._thread.join(timeout=2.0)
            self._thread = None


class PoseBroadcaster:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._frame_subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._camera = LatestFrameCamera()
        self._estimator = None
        # Held here as well as on the estimator so a chosen side survives a
        # stop()/start() cycle, which throws the estimator away.
        self._side: str | None = None
        self.control_link = PoseControlLink()

    def set_side(self, side: str | None) -> None:
        """Force tracking to one arm ('left'/'right'), or None to auto-pick."""
        # Validate before storing, so a bad value can't be silently persisted
        # and then re-applied on the next start().
        validate_side(side)
        self._side = side
        if self._estimator is not None:
            self._estimator.set_side(side)

    @property
    def side(self) -> str | None:
        return self._side

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def subscribe_frames(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._frame_subscribers.add(queue)
        return queue

    def unsubscribe_frames(self, queue: asyncio.Queue) -> None:
        self._frame_subscribers.discard(queue)

    @staticmethod
    def _publish_latest(subscribers: set[asyncio.Queue], payload) -> None:
        for queue in list(subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

    def _publish_frame(self, jpeg: bytes) -> None:
        self._publish_latest(self._frame_subscribers, jpeg)

    async def frames(self):
        """Yield JPEG preview frames captured by this broadcaster's camera."""
        queue = self.subscribe_frames()
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe_frames(queue)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._camera.start()
        self._estimator = ArmPoseEstimator(side=self._side)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._camera.close()
        if self._estimator is not None:
            self._estimator.close()
            self._estimator = None
        self.control_link.close()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last_sequence = 0

        while True:
            latest = self._camera.latest()
            if latest is None:
                # Camera not yet delivering; the capture thread handles opening
                # and retrying, so just wait for it.
                await asyncio.sleep(0.1)
                continue

            frame, sequence = latest
            if sequence == last_sequence:
                # No new frame since last pass. Re-running inference on the same
                # image would burn CPU to republish an identical pose, so wait a
                # fraction of a frame period instead of spinning.
                await asyncio.sleep(FRAME_INTERVAL_S / 4)
                continue
            last_sequence = sequence

            # Encode only while somebody is watching the preview. This is the
            # same frame used for pose below, so the browser never opens a
            # second handle to the physical camera.
            if self._frame_subscribers:
                encoded_ok, jpeg = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 78],
                )
                if encoded_ok:
                    self._publish_frame(jpeg.tobytes())

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # Off the event loop: inference is ~13ms and would otherwise stall
            # the mjpeg preview and WebSocket sends for that long every frame.
            # Awaited one at a time, so the estimator (stateful, VIDEO mode, and
            # requiring monotonic timestamps) still sees strictly ordered frames.
            reading = await loop.run_in_executor(None, self._estimator.read, rgb)
            # Published on every frame, including the ones with no landmarks.
            # Previously a lost arm published nothing at all, so the client just
            # kept rendering its last pose — indistinguishable from a freeze,
            # with no way to tell the user to get their arm back in frame.
            payload = {
                "type": "pose",
                "landmarks": (
                    {k: list(v) for k, v in reading.landmarks.items()}
                    if reading.landmarks
                    else None
                ),
                # Metric (metres) joints. The twin uses these, not the normalized
                # ones: normalized x/y are fractions of the image but z is on its
                # own unrelated scale — measured live, |dz| averaged 1.741
                # against |dx| 0.035 and |dy| 0.075, i.e. 23x too large. That
                # rendered every arm as if it pointed almost straight down the
                # lens, and blew the auto-framing past its widest setting on
                # every frame. World landmarks put all three axes in metres.
                "world_landmarks": (
                    {k: list(v) for k, v in reading.world_landmarks.items()}
                    if reading.world_landmarks
                    else None
                ),
                "side": reading.side,
                "status": reading.status,
            }
            self._publish_latest(self._subscribers, payload)

            # Separate feed with the opposite contract to the one above: the UI
            # is told about a lost arm, the control loop must hear nothing at
            # all (POSE_API.md req. 2). send() enforces that itself — do not add
            # a fallback here. Fire-and-forget: it never raises.
            self.control_link.send(reading)

            # No pacing sleep: the rate is now set by how fast the camera
            # actually delivers new frames, which is the real ceiling. Throttling
            # here on top of that is what previously dropped a 30fps camera to
            # ~15Hz. The sequence check above is what prevents a busy loop.
            await asyncio.sleep(0)
