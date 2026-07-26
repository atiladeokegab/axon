"""Route Live Twin's pad firings to the real ESP32 instead of the mock.

THE PROBLEM THIS SOLVES. Live Twin was built against a placeholder TCP driver:
it sends {"pad": "BICEP", "intensity": 60, "duration_ms": 800} to port 5005 and
prints "[MOCK] would fire" because no such board exists. Our board speaks a
different protocol entirely - a UDP duty vector on 8080 with a shared token -
so the two never met. This adapter presents the TensClient interface that
ActuationController expects, and speaks our protocol underneath.

WHY THIS IS GATED HARDER THAN THE REST OF THE SYSTEM.

The pads can be fired by the conversational agent. That means a voice command,
or a mis-heard one, can put current through somebody. Everything else in this
product needs a human to press ARM first; this path could otherwise bypass that
entirely, so it is off unless AXON_PAD_FIRING=1 is set deliberately.

ONE WRITER TO THE BOARD AT A TIME. The Human Control loop sends a fresh duty
vector 30 times a second. If this fired while that loop was running, the two
would overwrite each other every few milliseconds and the arm would judder
between two different intentions. So this refuses while the loop holds the
link, exactly as the camera has a single owner.

WHAT IT DOES NOT DO. It cannot exceed the board's limits, because it does not
enforce them - the firmware does. Duty ceiling, burst/cooldown, antagonist
refusal and the 500 ms watchdog all still apply, and a pad firing that asks for
too much simply gets clamped on the board.
"""

import asyncio
import logging
import os
import threading

import human_control  # noqa: F401  - installs the controller/ import bridge

import settings as C
import mapping
from link import EspLink

logger = logging.getLogger("pad_driver")

# Live Twin's pad names -> our channels. The wrist pair maps onto the grip
# channels: both are forearm flexor/extensor pads, which is the same muscle
# group described from two different vocabularies.
PAD_TO_CHANNEL = {
    "BICEP": "CH1",
    "TRICEP": "CH2",
    "FRONT_DELT": "CH3",
    "REAR_DELT": "CH4",
    "WRIST_FLEX": "CH7",
    "WRIST_EXTEND": "CH8",
}

# The firmware watchdog opens every relay after 500 ms of silence, so a "hold
# for 800 ms" cannot be one packet - it has to be re-sent for the duration.
RESEND_HZ = 30


def pad_firing_enabled() -> bool:
    return os.environ.get("AXON_PAD_FIRING", "").strip() in ("1", "true", "True")


class PadFiringRefused(RuntimeError):
    pass


class BoardPadDriver:
    """TensClient-shaped, but drives the real board through our link."""

    def __init__(self, service=None, host=None):
        # `service` is the Human Control service, consulted only to find out
        # whether it currently owns the board. Not used to send anything.
        self._service = service
        self._host = host
        self._link = None
        # Thread primitives, not asyncio ones: fire() and stop() arrive on
        # worker threads with no event loop of their own.
        self._fire_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self.mock = not pad_firing_enabled()

        if self.mock:
            logger.info(
                "pad firing DISABLED (set AXON_PAD_FIRING=1 to enable). "
                "Live Twin pad commands will be logged, not delivered."
            )

    # ---- guards -----------------------------------------------------------
    def _refuse_if_busy(self):
        if self._service is None:
            return
        try:
            running = bool(self._service.status().get("running"))
            simulated = bool(self._service.status().get("simulated", True))
        except Exception:
            return
        if running and not simulated:
            raise PadFiringRefused(
                "the Human Control loop is driving the board - stop it before "
                "firing pads, or the two will overwrite each other 30 times a "
                "second"
            )

    def _ensure_link(self):
        if self._link is None:
            self._link = EspLink(host=self._host)
        return self._link

    # ---- the TensClient interface ----------------------------------------
    def fire(self, pad: str, intensity: int = 60, duration_ms: int = 800) -> dict:
        channel = PAD_TO_CHANNEL.get(pad)
        if channel is None:
            raise PadFiringRefused("no channel is wired for pad %r" % (pad,))

        # Intensity is 0-100 in their vocabulary; ours is a duty fraction. The
        # board clamps again, so this is a translation and not a safety check.
        duty = max(0.0, min(1.0, float(intensity) / 100.0)) * C.DUTY_MAX

        if self.mock:
            logger.info("[MOCK] pad %s -> %s duty %.2f for %d ms "
                        "(AXON_PAD_FIRING not set)", pad, channel, duty, duration_ms)
            return {"status": "ok", "mock": True, "channel": channel, "duty": duty}

        self._refuse_if_busy()

        # One firing at a time: a second overlapping pulse would mean two
        # writers sending different duty vectors to the same board. A lock
        # rather than a task handle, because this runs in a worker thread.
        if not self._fire_lock.acquire(blocking=False):
            raise PadFiringRefused("a pad is already firing")

        # ActuationController calls this via asyncio.to_thread, so there is no
        # event loop in THIS thread and asyncio.create_task raises "no running
        # event loop". The driver is built at import time, before any loop
        # exists, so there is also no loop to hand it. Running the hold on a
        # private loop here sidesteps both: the caller's loop is never blocked,
        # because the caller is already awaiting us in a thread.
        try:
            self._stop_evt.clear()
            asyncio.run(self._hold(channel, duty, duration_ms))
        finally:
            self._fire_lock.release()

        return {"status": "ok", "channel": channel, "duty": round(duty, 3),
                "duration_ms": duration_ms}

    async def _hold(self, channel, duty, duration_ms):
        link = self._ensure_link()
        duties = {ch: 0.0 for ch in mapping.CHANNEL_ORDER}
        duties[channel] = duty
        vector = mapping.duties_to_list(duties)
        zeros = mapping.duties_to_list({ch: 0.0 for ch in mapping.CHANNEL_ORDER})

        period = 1.0 / RESEND_HZ
        ticks = max(1, int((duration_ms / 1000.0) * RESEND_HZ))
        try:
            link.arm()
            for _ in range(ticks):
                # Checked every tick rather than relying on task cancellation:
                # stop() is called from a different thread than this loop.
                if self._stop_evt.is_set():
                    break
                link.send_duties(vector, False)
                await asyncio.sleep(period)
        finally:
            # Always land on zero and disarm, even if cancelled. Relying on the
            # watchdog would work but takes up to 500 ms and leaves the board
            # armed, which is a worse resting state than an explicit stop.
            try:
                link.send_duties(zeros, False)
                link.disarm()
            except Exception:
                logger.exception("failed to release the board after a pad firing")

    def stop(self) -> dict:
        if self.mock:
            logger.info("[MOCK] pad STOP")
            return {"status": "ok", "mock": True}
        # Tells an in-flight _hold to stop resending. Its finally: block zeroes
        # and disarms, and this does the same below in case nothing was firing.
        self._stop_evt.set()
        try:
            link = self._ensure_link()
            link.send_duties(
                mapping.duties_to_list({ch: 0.0 for ch in mapping.CHANNEL_ORDER}),
                False)
            link.disarm()
        except Exception as exc:
            logger.warning("stop() could not reach the board: %s", exc)
        return {"status": "ok"}
