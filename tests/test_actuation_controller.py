import asyncio
import time

import pytest

from backend.actuation.controller import ActuationController, ActuationError
from backend.driver.tens_client import TensClientError
from backend import config


class _FakeDriver:
    """Duck-typed stand-in for TensClient — lets controller behavior be
    tested without a real/mock socket layer underneath."""

    def __init__(self, mock=True, fire_error=None, fire_delay=0.0, stop_error=None):
        self.mock = mock
        self.fire_calls = []
        self.stop_calls = 0
        self._fire_error = fire_error
        self._fire_delay = fire_delay
        self._stop_error = stop_error

    def fire(self, pad, intensity, duration_ms):
        if self._fire_delay:
            time.sleep(self._fire_delay)
        self.fire_calls.append((pad, intensity, duration_ms))
        if self._fire_error:
            raise self._fire_error
        return {"status": "ok"}

    def stop(self):
        self.stop_calls += 1
        if self._stop_error:
            raise self._stop_error
        return {"status": "ok"}


def test_armed_reflects_driver_mock_flag():
    assert ActuationController(_FakeDriver(mock=True)).armed is False
    assert ActuationController(_FakeDriver(mock=False)).armed is True


def test_fire_rejects_pad_not_in_allowlist():
    controller = ActuationController(_FakeDriver())
    with pytest.raises(ActuationError, match="allowlist"):
        asyncio.run(controller.fire("NOT_A_REAL_PAD"))


def test_fire_rejects_intensity_out_of_bounds():
    controller = ActuationController(_FakeDriver())
    with pytest.raises(ActuationError, match="intensity"):
        asyncio.run(controller.fire(config.PAD_BICEP, intensity=config.MAX_INTENSITY + 1))


def test_fire_rejects_duration_out_of_bounds():
    controller = ActuationController(_FakeDriver())
    with pytest.raises(ActuationError, match="duration_ms"):
        asyncio.run(controller.fire(config.PAD_BICEP, duration_ms=config.MAX_DURATION_MS + 1))


def test_fire_wraps_driver_error_as_actuation_error():
    driver = _FakeDriver(fire_error=TensClientError("board rejected command"))
    controller = ActuationController(driver)

    with pytest.raises(ActuationError, match="board rejected command"):
        asyncio.run(controller.fire(config.PAD_BICEP))


def test_concurrent_fire_rejects_second_call_while_first_in_flight():
    driver = _FakeDriver(fire_delay=0.2)
    controller = ActuationController(driver)

    async def main():
        first = asyncio.create_task(controller.fire(config.PAD_BICEP))
        await asyncio.sleep(0.05)  # let `first` acquire the lock and start its "slow" call
        with pytest.raises(ActuationError, match="already in flight"):
            await controller.fire(config.PAD_TRICEP)
        await first

    asyncio.run(main())
    assert driver.fire_calls == [(config.PAD_BICEP, config.DEFAULT_INTENSITY, config.DEFAULT_DURATION_MS)]


def test_cooldown_rejects_immediate_second_call(monkeypatch):
    monkeypatch.setattr(config, "ACTUATION_COOLDOWN_S", 10.0)
    driver = _FakeDriver()
    controller = ActuationController(driver)

    async def main():
        await controller.fire(config.PAD_BICEP)
        with pytest.raises(ActuationError, match="cooldown"):
            await controller.fire(config.PAD_TRICEP)

    asyncio.run(main())


def test_stop_never_raises_even_if_driver_stop_fails():
    driver = _FakeDriver(stop_error=TensClientError("board unreachable"))
    controller = ActuationController(driver)

    asyncio.run(controller.stop())  # must not raise

    assert driver.stop_calls == 1
