import asyncio

import pytest

from live_twin.backend.main import LiveRuntime


class FakeBroadcaster:
    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.starts = 0
        self.stops = 0

    async def start(self):
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("model unavailable")

    async def stop(self):
        self.stops += 1


class FakeController:
    def __init__(self):
        self.stops = 0

    async def stop(self):
        self.stops += 1


def test_runtime_starts_once_and_stops_after_final_client():
    async def scenario():
        broadcaster = FakeBroadcaster()
        controller = FakeController()
        runtime = LiveRuntime(broadcaster, controller)

        await runtime.acquire()
        await runtime.acquire()
        assert runtime.active_clients == 2
        assert broadcaster.starts == 1

        await runtime.release()
        assert broadcaster.stops == 0

        await runtime.release()
        assert runtime.active_clients == 0
        assert broadcaster.stops == 1
        assert controller.stops == 1

    asyncio.run(scenario())


def test_runtime_cleans_up_a_partial_start_failure():
    async def scenario():
        broadcaster = FakeBroadcaster(fail_start=True)
        runtime = LiveRuntime(broadcaster, FakeController())

        with pytest.raises(RuntimeError, match="model unavailable"):
            await runtime.acquire()

        assert runtime.active_clients == 0
        assert broadcaster.starts == 1
        assert broadcaster.stops == 1

    asyncio.run(scenario())


def test_runtime_shutdown_is_idempotent_and_forces_release():
    async def scenario():
        broadcaster = FakeBroadcaster()
        controller = FakeController()
        runtime = LiveRuntime(broadcaster, controller)

        await runtime.acquire()
        await runtime.shutdown()
        await runtime.shutdown()

        assert runtime.active_clients == 0
        assert broadcaster.stops == 2
        assert controller.stops == 2

    asyncio.run(scenario())
