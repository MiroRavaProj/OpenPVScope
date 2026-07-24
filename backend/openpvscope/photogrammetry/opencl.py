"""Best-effort OpenCL availability probe (diagnostics / health API)."""

from __future__ import annotations

import shutil
import subprocess


def probe_opencl() -> dict:
    """Best-effort OpenCL availability check."""
    try:
        import pyopencl as cl  # type: ignore

        platforms = cl.get_platforms()
        devices = []
        for p in platforms:
            for d in p.get_devices():
                devices.append({"platform": p.name, "device": d.name, "type": str(d.type)})
        return {"available": bool(devices), "devices": devices}
    except Exception as e:
        clinfo = shutil.which("clinfo")
        if clinfo:
            try:
                r = subprocess.run([clinfo, "-l"], capture_output=True, text=True, timeout=10)
                ok = r.returncode == 0 and bool(r.stdout.strip())
                return {"available": ok, "devices": [], "clinfo": r.stdout[:2000]}
            except Exception:
                pass
        return {"available": False, "devices": [], "error": str(e)}
