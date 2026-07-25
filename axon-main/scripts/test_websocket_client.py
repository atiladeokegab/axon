"""
Quick manual/automated smoke test for the full WebSocket loop: connects,
sends a select_motion message, prints whatever comes back for a few seconds.

Run the server first: uv run uvicorn backend.main:app
Then:                 uv run python scripts/test_websocket_client.py grip
"""
import asyncio
import json
import sys

import websockets


async def main(motion: str):
    uri = "ws://127.0.0.1:8000/ws"
    print(f"connecting to {uri} ...")
    async with websockets.connect(uri) as ws:
        print(f"connected. sending select_motion={motion!r}")
        await ws.send(json.dumps({"type": "select_motion", "motion": motion}))

        try:
            async with asyncio.timeout(3):
                while True:
                    raw = await ws.recv()
                    print("<-", raw)
        except (TimeoutError, asyncio.TimeoutError):
            print("(done listening, 3s window closed)")


if __name__ == "__main__":
    motion = sys.argv[1] if len(sys.argv) > 1 else "grip"
    asyncio.run(main(motion))
