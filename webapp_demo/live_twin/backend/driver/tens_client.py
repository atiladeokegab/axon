"""
TCP client for the TENS driver board.

Protocol (ASSUMED — confirm exact field names/port with Jack & Faisal):
    Request:  {"pad": "BICEP", "intensity": 60, "duration_ms": 800}\n
    Response: {"status": "ok", ...}\n — must be JSON with a "status" field;
              anything else (non-JSON, missing status, status != "ok") is
              treated as a failure, not silently accepted.

Defaults to mock mode (config.DRIVER_MOCK_MODE). Constructing with mock=False
calls config.assert_real_mode_allowed(), which raises unless the board
contract and relay pairing have been explicitly confirmed — see config.py.
"""
import json
import socket
import logging

from live_twin.backend import config

logger = logging.getLogger("tens_client")


class TensClientError(Exception):
    pass


class TensClient:
    def __init__(self, host: str = None, port: int = None, mock: bool = None, timeout: float = 2.0):
        self.host = host or config.BOARD_HOST
        self.port = port or config.BOARD_PORT
        self.mock = config.DRIVER_MOCK_MODE if mock is None else mock
        self.timeout = timeout

        if not self.mock:
            config.assert_real_mode_allowed(self.host)

    def fire(self, pad: str, intensity: int = config.DEFAULT_INTENSITY,
             duration_ms: int = config.DEFAULT_DURATION_MS) -> dict:
        self._validate_command(pad, intensity, duration_ms)
        payload = {"pad": pad, "intensity": intensity, "duration_ms": duration_ms}

        if self.mock:
            logger.info("[MOCK] would fire: %s", payload)
            return {"status": "ok", "mock": True, "sent": payload}

        return self._send(payload)

    def stop(self) -> dict:
        """De-energize. Always allowed regardless of pad allowlist/bounds — a
        STOP command is never something worth blocking."""
        payload = {"pad": "ALL", "intensity": 0, "duration_ms": 0}
        if self.mock:
            logger.info("[MOCK] would send STOP")
            return {"status": "ok", "mock": True, "sent": payload}
        return self._send(payload)

    def _validate_command(self, pad: str, intensity: int, duration_ms: int) -> None:
        if pad not in config.ALL_PAD_NAMES:
            raise TensClientError(f"pad {pad!r} is not in the confirmed pad allowlist {sorted(config.ALL_PAD_NAMES)}")
        if not (config.MIN_INTENSITY <= intensity <= config.MAX_INTENSITY):
            raise TensClientError(
                f"intensity {intensity} outside allowed range [{config.MIN_INTENSITY}, {config.MAX_INTENSITY}]"
            )
        if not (config.MIN_DURATION_MS <= duration_ms <= config.MAX_DURATION_MS):
            raise TensClientError(
                f"duration_ms {duration_ms} outside allowed range "
                f"[{config.MIN_DURATION_MS}, {config.MAX_DURATION_MS}]"
            )

    def _send(self, payload: dict) -> dict:
        message = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(message)
                raw = sock.recv(4096)
        except OSError as exc:
            raise TensClientError(f"failed to reach board at {self.host}:{self.port}: {exc}") from exc

        if not raw:
            raise TensClientError("board closed connection with no response")

        text = raw.decode("utf-8", errors="replace").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TensClientError(f"board sent a non-JSON response: {text!r}") from exc

        if not isinstance(parsed, dict) or "status" not in parsed:
            raise TensClientError(f"board response missing a 'status' field: {parsed!r}")
        if parsed["status"] != "ok":
            raise TensClientError(f"board reported failure: {parsed!r}")

        return parsed
