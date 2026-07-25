import json
import socket
import threading

import pytest

from backend.driver.tens_client import TensClient, TensClientError
from backend import config


def test_mock_fire_returns_ok_without_a_real_socket():
    client = TensClient(mock=True)

    result = client.fire(config.PAD_BICEP, intensity=50, duration_ms=500)

    assert result["status"] == "ok"
    assert result["mock"] is True
    assert result["sent"]["pad"] == config.PAD_BICEP


def test_mock_stop_does_not_open_a_socket():
    client = TensClient(mock=True)

    result = client.stop()

    assert result["status"] == "ok"
    assert result["mock"] is True


def test_fire_rejects_pad_not_in_allowlist():
    client = TensClient(mock=True)

    with pytest.raises(TensClientError, match="allowlist"):
        client.fire("NOT_A_REAL_PAD")


def test_fire_rejects_intensity_out_of_bounds():
    client = TensClient(mock=True)

    with pytest.raises(TensClientError, match="intensity"):
        client.fire(config.PAD_BICEP, intensity=config.MAX_INTENSITY + 1)


def test_fire_rejects_duration_out_of_bounds():
    client = TensClient(mock=True)

    with pytest.raises(TensClientError, match="duration_ms"):
        client.fire(config.PAD_BICEP, duration_ms=config.MAX_DURATION_MS + 1)


def test_real_mode_refuses_to_construct_without_confirmation(monkeypatch):
    monkeypatch.setattr(config, "BOARD_CONTRACT_CONFIRMED", False)
    monkeypatch.setattr(config, "RELAY_PAIRING_CONFIRMED", False)

    with pytest.raises(RuntimeError, match="Refusing to run"):
        TensClient(mock=False, host="10.0.0.5")


def test_real_mode_refuses_placeholder_host_even_if_confirmed(monkeypatch):
    monkeypatch.setattr(config, "BOARD_CONTRACT_CONFIRMED", True)
    monkeypatch.setattr(config, "RELAY_PAIRING_CONFIRMED", True)

    with pytest.raises(RuntimeError, match="placeholder"):
        TensClient(mock=False, host=config._PLACEHOLDER_BOARD_HOST)


class _FakeBoard:
    """A minimal local TCP server standing in for the real board, so the
    wire protocol (newline-delimited JSON, response validation) is tested
    against an actual socket instead of only mocked."""

    def __init__(self, respond_with: bytes | None, close_without_reply: bool = False):
        self.respond_with = respond_with
        self.close_without_reply = close_without_reply
        self.received: list[bytes] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve_once, daemon=True)
        self._thread.start()

    def _serve_once(self):
        conn, _ = self._sock.accept()
        with conn:
            data = conn.recv(4096)
            self.received.append(data)
            if not self.close_without_reply and self.respond_with is not None:
                conn.sendall(self.respond_with)

    def close(self):
        self._sock.close()


@pytest.fixture(autouse=True)
def _confirmed_real_mode(monkeypatch):
    monkeypatch.setattr(config, "BOARD_CONTRACT_CONFIRMED", True)
    monkeypatch.setattr(config, "RELAY_PAIRING_CONFIRMED", True)


def test_real_fire_sends_newline_delimited_json_and_parses_ack():
    board = _FakeBoard(respond_with=b'{"status": "ok", "pad": "BICEP"}\n')
    try:
        client = TensClient(host="127.0.0.1", port=board.port, mock=False, timeout=2.0)
        result = client.fire(config.PAD_BICEP, intensity=50, duration_ms=500)
    finally:
        board.close()

    assert result["status"] == "ok"
    sent = json.loads(board.received[0].decode().strip())
    assert sent == {"pad": config.PAD_BICEP, "intensity": 50, "duration_ms": 500}


def test_real_fire_raises_on_nack_status():
    board = _FakeBoard(respond_with=b'{"status": "error", "reason": "channel busy"}\n')
    try:
        client = TensClient(host="127.0.0.1", port=board.port, mock=False, timeout=2.0)
        with pytest.raises(TensClientError, match="failure"):
            client.fire(config.PAD_BICEP)
    finally:
        board.close()


def test_real_fire_raises_on_non_json_response():
    board = _FakeBoard(respond_with=b"not json at all\n")
    try:
        client = TensClient(host="127.0.0.1", port=board.port, mock=False, timeout=2.0)
        with pytest.raises(TensClientError, match="non-JSON"):
            client.fire(config.PAD_BICEP)
    finally:
        board.close()


def test_real_fire_raises_on_response_missing_status_field():
    board = _FakeBoard(respond_with=b'{"pad": "BICEP"}\n')
    try:
        client = TensClient(host="127.0.0.1", port=board.port, mock=False, timeout=2.0)
        with pytest.raises(TensClientError, match="status"):
            client.fire(config.PAD_BICEP)
    finally:
        board.close()


def test_real_fire_raises_when_board_closes_without_reply():
    board = _FakeBoard(respond_with=None, close_without_reply=True)
    try:
        client = TensClient(host="127.0.0.1", port=board.port, mock=False, timeout=2.0)
        with pytest.raises(TensClientError, match="no response"):
            client.fire(config.PAD_BICEP)
    finally:
        board.close()


def test_real_fire_raises_when_nothing_is_listening():
    # bind + close to get a port nothing is listening on
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    client = TensClient(host="127.0.0.1", port=port, mock=False, timeout=1.0)
    with pytest.raises(TensClientError, match="failed to reach board"):
        client.fire(config.PAD_BICEP)
