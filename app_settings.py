"""User-configurable data directories.

When frozen into an exe, the program folder may be read-only, so all writable
data (PlatformIO venv, toolchain cache, config overrides, firmware copies)
lives under a user-chosen workspace. Settings themselves are stored in APPDATA.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_DIR_NAME = "EdgeTXBuilder"
SETTINGS_FILE = "settings.json"


def module_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_workspace() -> Path:
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return module_dir()


def settings_file() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME / SETTINGS_FILE
    return default_workspace() / SETTINGS_FILE


def load() -> dict:
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def workspace() -> Path:
    value = load().get("workspace")
    return Path(value) if value else default_workspace()


def set_workspace(path: Path) -> None:
    data = load()
    data["workspace"] = str(path)
    save(data)


def reset_workspace() -> None:
    data = load()
    data.pop("workspace", None)
    save(data)


def configured(key: str) -> Path | None:
    value = load().get(key)
    return Path(value) if value else None


def set_configured(key: str, path: Path | None) -> None:
    data = load()
    if path:
        data[key] = str(path)
    else:
        data.pop(key, None)
    save(data)


def pio_venv_dir() -> Path:
    custom = configured("pio_venv")
    if custom:
        return custom
    legacy = module_dir() / "tools" / "pio-venv"
    if legacy.is_dir():
        return legacy
    workspace_tools = workspace() / "tools" / "pio-venv"
    if workspace_tools.is_dir():
        return workspace_tools
    return workspace() / "pio-venv"


def pio_core_enabled() -> bool:
    return bool(load().get("pio_core_enabled"))


def set_pio_core_enabled(enabled: bool) -> None:
    data = load()
    data["pio_core_enabled"] = bool(enabled)
    save(data)


def pio_core_dir() -> Path | None:
    if not pio_core_enabled():
        return None
    return configured("pio_core") or workspace() / "pio-core"


def overrides_dir() -> Path:
    return configured("overrides") or workspace() / "config_overrides"


def output_dir() -> Path:
    return configured("output") or workspace() / "output"


def active_layout() -> dict | None:
    value = load().get("active_layout")
    return value if isinstance(value, dict) else None


def set_active_layout(data: dict) -> None:
    settings = load()
    settings["active_layout"] = data
    save(settings)


def clear_active_layout() -> None:
    settings = load()
    settings.pop("active_layout", None)
    save(settings)


def paths() -> dict[str, Path | None]:
    return {
        "workspace": workspace(),
        "pio_venv": pio_venv_dir(),
        "pio_core": pio_core_dir(),
        "overrides": overrides_dir(),
        "output": output_dir(),
    }
