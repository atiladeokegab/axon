import pytest

from backend import config


def test_real_mode_blocked_by_default():
    with pytest.raises(RuntimeError):
        config.assert_real_mode_allowed()


def test_real_mode_blocked_by_placeholder_host_even_when_confirmed(monkeypatch):
    monkeypatch.setattr(config, "BOARD_HOST", config._PLACEHOLDER_BOARD_HOST)
    monkeypatch.setattr(config, "BOARD_CONTRACT_CONFIRMED", True)
    monkeypatch.setattr(config, "RELAY_PAIRING_CONFIRMED", True)

    with pytest.raises(RuntimeError, match="placeholder"):
        config.assert_real_mode_allowed()


def test_real_mode_blocked_by_unconfirmed_contract(monkeypatch):
    monkeypatch.setattr(config, "BOARD_HOST", "10.0.0.5")
    monkeypatch.setattr(config, "BOARD_CONTRACT_CONFIRMED", False)
    monkeypatch.setattr(config, "RELAY_PAIRING_CONFIRMED", True)

    with pytest.raises(RuntimeError, match="BOARD_CONTRACT_CONFIRMED"):
        config.assert_real_mode_allowed()


def test_real_mode_blocked_by_unconfirmed_relay_pairing(monkeypatch):
    monkeypatch.setattr(config, "BOARD_HOST", "10.0.0.5")
    monkeypatch.setattr(config, "BOARD_CONTRACT_CONFIRMED", True)
    monkeypatch.setattr(config, "RELAY_PAIRING_CONFIRMED", False)

    with pytest.raises(RuntimeError, match="RELAY_PAIRING_CONFIRMED"):
        config.assert_real_mode_allowed()


def test_real_mode_allowed_once_everything_is_confirmed(monkeypatch):
    monkeypatch.setattr(config, "BOARD_HOST", "10.0.0.5")
    monkeypatch.setattr(config, "BOARD_CONTRACT_CONFIRMED", True)
    monkeypatch.setattr(config, "RELAY_PAIRING_CONFIRMED", True)

    config.assert_real_mode_allowed()  # must not raise
