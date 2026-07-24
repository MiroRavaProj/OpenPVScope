"""App-wide settings (not project-specific) under ~/.openpvscope/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from openpvscope.io_atomic import atomic_write_json

OpszMode = Literal["full", "light"]
AppLanguage = Literal["en", "it", "es", "de", "fr"]


def config_dir() -> Path:
    return Path.home() / ".openpvscope"


def settings_path() -> Path:
    return config_dir() / "settings.json"


class RecentProject(BaseModel):
    path: str
    name: str
    opened_at: str


class AppSettings(BaseModel):
    """General preferences for OpenPVScope."""

    history_max_steps: int = Field(default=30, ge=1, le=200)
    history_include_rasters: bool = True
    default_project_dir: str | None = None
    recent_max: int = Field(default=12, ge=0, le=50)
    recent_projects: list[RecentProject] = Field(default_factory=list)
    opsz_default_mode: OpszMode = "full"
    # Relative path prefixes excluded from light .opsz exports
    opsz_light_exclude: list[str] = Field(
        default_factory=lambda: ["work/", "photogrammetry/", ".openpvscope_history/"]
    )
    language: AppLanguage = "en"
    # User dismissed the "Install ODX?" modal (GeoTIFF-only until they install)
    odx_install_prompt_dismissed: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.is_file():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AppSettings.model_validate(data)
    except Exception:
        return AppSettings()


def save_settings(settings: AppSettings) -> AppSettings:
    config_dir().mkdir(parents=True, exist_ok=True)
    atomic_write_json(settings_path(), settings.model_dump(mode="json"))
    return settings


def update_settings(patch: dict[str, Any]) -> AppSettings:
    current = load_settings().model_dump(mode="json")
    for k, v in patch.items():
        current[k] = v
    settings = AppSettings.model_validate(current)
    return save_settings(settings)


def add_recent_project(opsx_path: Path | str, name: str) -> AppSettings:
    settings = load_settings()
    path = str(Path(opsx_path).resolve())
    entries = [r for r in settings.recent_projects if r.path != path]
    entries.insert(0, RecentProject(path=path, name=name, opened_at=_utc_now()))
    settings.recent_projects = entries[: max(0, settings.recent_max)]
    return save_settings(settings)


def clear_recent_projects() -> AppSettings:
    settings = load_settings()
    settings.recent_projects = []
    return save_settings(settings)
