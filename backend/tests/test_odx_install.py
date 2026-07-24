"""Tests for ODX install state helpers (no network download)."""

from __future__ import annotations

from openpvscope.photogrammetry import odx_install


def test_get_install_state_shape() -> None:
    st = odx_install.get_install_state()
    assert "status" in st
    assert st["status"] in ("idle", "running", "done", "error")
    assert "odx" in st
    assert "available" in st["odx"]


def test_start_when_already_available(monkeypatch) -> None:
    monkeypatch.setattr(
        odx_install,
        "probe_odx",
        lambda: {"available": True, "root": r"C:\ODX", "run_script": r"C:\ODX\run.bat", "error": None},
    )
    st = odx_install.start_odx_install()
    assert st["status"] == "done"
    assert st["odx"]["available"] is True
