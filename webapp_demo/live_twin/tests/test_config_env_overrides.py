"""
Placement geometry offsets and tape HSV bounds are the values most likely to
need real-world tuning on-site (once Siona/Amara's research or a real tape
photo comes in) without a code change/redeploy — these tests pin down that
they're actually read from the environment, like BOARD_HOST already is.
"""
import importlib

from live_twin.backend import config


def _reload():
    importlib.reload(config)


def test_wrist_pad_offset_pct_reads_env_override(monkeypatch):
    monkeypatch.setenv("WRIST_PAD_OFFSET_PCT", "0.15")
    try:
        _reload()
        assert config.WRIST_PAD_OFFSET_PCT == 0.15
    finally:
        monkeypatch.delenv("WRIST_PAD_OFFSET_PCT", raising=False)
        _reload()


def test_wrist_pad_offset_pct_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("WRIST_PAD_OFFSET_PCT", raising=False)
    _reload()
    assert config.WRIST_PAD_OFFSET_PCT == 0.08


def test_delt_pad_offset_pct_reads_env_override(monkeypatch):
    monkeypatch.setenv("DELT_PAD_OFFSET_PCT", "0.22")
    try:
        _reload()
        assert config.DELT_PAD_OFFSET_PCT == 0.22
    finally:
        monkeypatch.delenv("DELT_PAD_OFFSET_PCT", raising=False)
        _reload()


def test_marker_hsv_lower_reads_env_override(monkeypatch):
    monkeypatch.setenv("MARKER_HSV_LOWER", "10,50,50")
    try:
        _reload()
        assert config.MARKER_HSV_LOWER == (10, 50, 50)
    finally:
        monkeypatch.delenv("MARKER_HSV_LOWER", raising=False)
        _reload()


def test_marker_hsv_upper_reads_env_override(monkeypatch):
    monkeypatch.setenv("MARKER_HSV_UPPER", "25,255,255")
    try:
        _reload()
        assert config.MARKER_HSV_UPPER == (25, 255, 255)
    finally:
        monkeypatch.delenv("MARKER_HSV_UPPER", raising=False)
        _reload()


def test_marker_hsv_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("MARKER_HSV_LOWER", raising=False)
    monkeypatch.delenv("MARKER_HSV_UPPER", raising=False)
    _reload()
    assert config.MARKER_HSV_LOWER == (140, 80, 80)
    assert config.MARKER_HSV_UPPER == (170, 255, 255)


def test_marker_hsv_rejects_malformed_env_value(monkeypatch):
    import pytest

    monkeypatch.setenv("MARKER_HSV_LOWER", "not,three,valid,numbers")
    try:
        with pytest.raises(ValueError, match="MARKER_HSV_LOWER"):
            _reload()
    finally:
        monkeypatch.delenv("MARKER_HSV_LOWER", raising=False)
        _reload()
