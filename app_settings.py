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

APP_DIR_NAME = "OpenRadioBuilder"
LEGACY_APP_DIR_NAME = "EdgeTXBuilder"
SETTINGS_FILE = "settings.json"


def module_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def portable_root() -> Path | None:
    """Detect a development/portable folder that already has tools/pio-venv."""
    for base in (module_dir(), module_dir().parent):
        if (base / "tools" / "pio-venv").is_dir():
            return base
    return None


def default_workspace() -> Path:
    if getattr(sys, "frozen", False):
        portable = portable_root()
        if portable:
            return portable
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return module_dir()


def settings_file() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME / SETTINGS_FILE
    return default_workspace() / SETTINGS_FILE


def legacy_settings_file() -> Path | None:
    base = os.environ.get("APPDATA")
    if not base:
        return None
    return Path(base) / LEGACY_APP_DIR_NAME / SETTINGS_FILE


def load() -> dict:
    path = settings_file()
    for candidate in (path, legacy_settings_file()):
        if candidate is None or not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if candidate != path:
            save(data)
        return data
    return {}


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


SOURCE_KEYS = {"edgetx": "edgetx_source", "elrs": "elrs_source"}
SOURCE_DIR_NAMES = {"edgetx": ("edgetx",), "elrs": ("ExpressLRS",)}


def is_source_dir(kind: str, path: Path) -> bool:
    """Recognize a standard GitHub checkout of EdgeTX / ExpressLRS."""
    if kind == "edgetx":
        return ((path / "CMakeLists.txt").is_file()
                and (path / "radio" / "src" / "CMakeLists.txt").is_file())
    return ((path / "src" / "platformio.ini").is_file()
            or (path / "platformio.ini").is_file())


def saved_source(kind: str) -> Path | None:
    key = SOURCE_KEYS.get(kind)
    path = configured(key) if key else None
    return path if path and path.is_dir() else None


def save_source(kind: str, value: str | Path | None) -> None:
    """Remember a chosen source directory; ignore incomplete/invalid input."""
    key = SOURCE_KEYS.get(kind)
    if not key:
        return
    text = str(value).strip() if value else ""
    path = Path(text) if text else None
    if path and is_source_dir(kind, path):
        set_configured(key, path)


def default_source(kind: str) -> str:
    """Startup value: last chosen directory, else a checkout near the workspace."""
    saved = saved_source(kind)
    if saved:
        return str(saved)
    bases: list[Path] = []
    for base in (workspace().parent, workspace().parent.parent, module_dir().parent):
        if base not in bases:
            bases.append(base)
    candidates: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            candidates.extend(
                child for child in base.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            )
        except OSError:
            continue
    names = tuple(name.lower() for name in SOURCE_DIR_NAMES.get(kind, ()))
    candidates.sort(key=lambda path: (path.name.lower() not in names, path.name.lower()))
    for candidate in candidates:
        if is_source_dir(kind, candidate):
            return str(candidate)
        nested = candidate / "ExpressLRS"
        if kind == "elrs" and is_source_dir(kind, nested):
            return str(nested)
    return ""


def paths() -> dict[str, Path | None]:
    return {
        "workspace": workspace(),
        "pio_venv": pio_venv_dir(),
        "pio_core": pio_core_dir(),
        "overrides": overrides_dir(),
        "output": output_dir(),
    }
