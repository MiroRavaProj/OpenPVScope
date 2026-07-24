"""ODX discovery and prepare_dataset (no full ODX install required)."""

from __future__ import annotations

from pathlib import Path

from openpvscope.photogrammetry.odx import ODXRunner, _is_odx_root, probe_odx


def test_probe_odx_shape():
    info = probe_odx()
    assert "available" in info
    assert "root" in info
    assert "error" in info
    if info["available"]:
        assert info["root"]
        assert Path(info["root"]).is_dir()
        assert _is_odx_root(Path(info["root"]))


def test_prepare_rgb_dataset(tmp_path: Path):
    # Minimal fake JPEG
    raw = tmp_path / "raw.jpg"
    raw.write_bytes(b"\xff\xd8\xff\xd9")
    project = tmp_path / "proj"
    project.mkdir()
    runner = ODXRunner(project, odx_root=None)
    ds = runner.prepare_dataset("rgb", [raw])
    assert (ds / "images" / "raw.jpg").is_file()
    meta = project / "photogrammetry" / "rgb_job.json"
    assert meta.is_file()
    text = meta.read_text(encoding="utf-8")
    assert '"engine": "odx"' in text
