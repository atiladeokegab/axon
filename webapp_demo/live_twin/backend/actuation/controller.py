"""
Safety/validation seam between the transport layer (WebSocket) and
TensClient. Nothing else should call TensClient.fire()/stop() directly —
this is the one place that:

- validates the pad against the confirmed allowlist and intensity/duration
  bounds (this duplicates TensClient's own checks on purpose — defense in
  depth, not trust that every caller remembers to check)
- serializes commands (single-flight: a command already in flight rejects a
  new one rather than queuing or interleaving) plus a cooldown between
  commands, so no client can spam actuation
- runs the blocking socket call off the asyncio event loop
- guarantees a best-effort STOP on cleanup (disconnect, error, shutdown)
- exposes whether it's actually armed (real mode) or safely mocked, so the
  UI can show that state unambiguously
"""
import asyncio
import logging
import time

from live_twin.backend import config
from live_twin.backend.driver.tens_client import TensClient, TensClientError

logger = logging.getLogger("actuation_controller")


class ActuationError(Exception):
    pass


class ActuationController:
    def __init__(self, driver: TensClient | None = None):
        self.driver = driver or TensClient()
        self._lock = asyncio.Lock()
        self._last_fire_at = 0.0

    @property
    def armed(self) -> bool:
        """True if this controller can actually energize real hardware."""
        return not self.driver.mock

    async def fire(self, pad: str, intensity: int = config.DEFAULT_INTENSITY,
                   duration_ms: int = config.DEFAULT_DURATION_MS) -> dict:
        if pad not in config.ALL_PAD_NAMES:
            raise ActuationError(f"pad {pad!r} is not in the confirmed pad allowlist")
        if not (config.MIN_INTENSITY <= intensity <= config.MAX_INTENSITY):
            raise ActuationError(f"intensity {intensity} outside [{config.MIN_INTENSITY}, {config.MAX_INTENSITY}]")
        if not (config.MIN_DURATION_MS <= duration_ms <= config.MAX_DURATION_MS):
            raise ActuationError(
                f"duration_ms {duration_ms} outside [{config.MIN_DURATION_MS}, {config.MAX_DURATION_MS}]"
            )

        if self._lock.locked():
            raise ActuationError("another actuation command is already in flight")

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_fire_at
            if elapsed < config.ACTUATION_COOLDOWN_S:
                raise ActuationError(f"cooldown active, {config.ACTUATION_COOLDOWN_S - elapsed:.2f}s remaining")

            try:
                result = await asyncio.to_thread(self.driver.fire, pad, intensity, duration_ms)
            except TensClientError as exc:
                raise ActuationError(str(exc)) from exc

            self._last_fire_at = time.monotonic()
            return result

    async def stop(self) -> None:
        """Best-effort de-energize. Safe to call repeatedly and never raises —
        callers use this from cleanup paths where an exception would just get
        swallowed anyway."""
        try:
            await asyncio.to_thread(self.driver.stop)
        except TensClientError:
            logger.exception("STOP failed to reach the board")
