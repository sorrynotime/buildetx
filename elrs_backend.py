"""ExpressLRS build backend (PlatformIO).

No GUI imports: the same functions serve the Tkinter app and the CLI.
Callbacks: on_line(text) receives one log line at a time.

CLI examples:
    python elrs_backend.py check
    python elrs_backend.py install
    python elrs_backend.py list --source E:\\Code\\ExpressLRS
    python elrs_backend.py build --source E:\\Code\\ExpressLRS --env <env>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path

import app_settings
from builder_utils import external_python, run_capture

DEFAULT_SOURCE = r"E:\Code\ExpressLRS"


def venv_dir() -> Path:
    return app_settings.pio_venv_dir()


def pio_env() -> dict[str, str]:
    env = os.environ.copy()
    core = app_settings.pio_core_dir()
    if core:
        env["PLATFORMIO_CORE_DIR"] = str(core)
    return env


def venv_python() -> Path:
    return venv_dir() / "Scripts" / "python.exe"


def pio_python() -> str | None:
    """Dedicated venv first, system python as fallback."""
    if venv_python().is_file():
        return str(venv_python())
    return external_python()


def pio_version(python: str | None = None) -> str | None:
    python = python or pio_python()
    if not python:
        return None
    code, output = run_capture(
        [python, "-m", "platformio", "--version"], env=pio_env()
    )
    if code != 0:
        return None
    return output.strip().splitlines()[-1] if output.strip() else None


def find_source(source: Path) -> Path | None:
    """Return the PlatformIO working directory for an ELRS checkout.

    Recent ExpressLRS trees keep platformio.ini in src/; older ones at root.
    """
    for candidate in (source / "src", source):
        if (candidate / "platformio.ini").is_file():
            return candidate
    return None


def check_environment(source: Path, on_line) -> bool:
    ok = True
    python = external_python()
    if python:
        code, output = run_capture([python, "--version"])
        on_line(f"{'OK' if code == 0 else '缺失'} Python: {output} ({python})")
    else:
        on_line("缺失 Python: 请先安装 Python 并加入 PATH。")
        ok = False
    code, output = run_capture(["git", "--version"])
    on_line(f"{'OK' if code == 0 else '缺失'} git: {output or '-'}")
    workdir = find_source(source)
    if workdir:
        on_line(f"OK   platformio.ini: {workdir}")
    else:
        on_line("缺失 platformio.ini：请选择 ELRS 源码根目录（ini 在根目录或 src 子目录）。")
        ok = False
    version = pio_version()
    if version:
        on_line(f"OK   {version} ({pio_python()})")
    else:
        on_line("缺失 PlatformIO：点击“安装 PlatformIO”，将安装到独立目录：")
        on_line(f"     {venv_dir()}")
        on_line("     不污染系统 Python，也不需要 VSCode。")
        ok = False
    return ok


def install_platformio(on_line) -> bool:
    python = external_python()
    if not python:
        on_line("失败：未在 PATH 找到 python，请先安装 Python。")
        return False
    existing = pio_version()
    if existing:
        on_line(f"已安装 {existing}，无需重复安装。")
        return True
    venv = venv_dir()
    on_line(f"创建独立虚拟环境: {venv}")
    code, _ = run_capture([python, "-m", "venv", str(venv)], on_line=on_line)
    if code != 0:
        on_line("失败：虚拟环境创建失败。")
        return False
    on_line("正在通过 pip 安装 PlatformIO（需要联网，请稍候）...")
    code, _ = run_capture(
        [str(venv_python()), "-m", "pip", "install", "platformio"], on_line=on_line
    )
    if code != 0:
        on_line("失败：PlatformIO 安装失败，请检查网络后重试。")
        return False
    version = pio_version()
    on_line(f"OK   安装完成: {version or '版本未知'}")
    return version is not None


def list_envs(source: Path) -> dict[str, list[str]]:
    """Group PlatformIO env names by TX/RX using pio's own config parser."""
    workdir = find_source(source)
    python = pio_python()
    if not workdir or not python or not pio_version(python):
        return {}
    code, output = run_capture(
        [python, "-m", "platformio", "project", "config", "--json-output"],
        cwd=workdir, env=pio_env(),
    )
    if code != 0:
        return {}
    start = output.find("[")
    if start < 0:
        return {}
    try:
        sections = json.loads(output[start:])
    except ValueError:
        return {}
    groups: dict[str, list[str]] = {"TX": [], "RX": []}
    for section in sections:
        name = str(section[0]) if isinstance(section, (list, tuple)) else str(section)
        if not name.startswith("env:"):
            continue
        env = name[4:]
        upper = env.upper()
        if "_TX" in upper:
            groups["TX"].append(env)
        elif "_RX" in upper:
            groups["RX"].append(env)
    return {kind: sorted(envs) for kind, envs in groups.items() if envs}


FILE_LOCK_MARKERS = (
    "WinError 32", "SHFileOperationW", "另一个程序正在使用此文件",
    "being used by another process",
)


def artifacts_dir(source: Path, env: str) -> Path | None:
    workdir = find_source(source)
    if not workdir or not env:
        return None
    return workdir / ".pio" / "build" / env


def build_firmware(
    source: Path, env: str, on_line, config: str | None = None
) -> tuple[bool, list[Path]]:
    workdir = find_source(source)
    python = pio_python()
    if not workdir:
        on_line("失败：未找到 platformio.ini。")
        return False, []
    if not python or not pio_version(python):
        on_line("失败：PlatformIO 不可用，请先安装。")
        return False, []
    child_env = pio_env()
    if config:
        child_env["ELRS_UNIFIED_CONFIG"] = config
        on_line(f"硬件配置: {config}（自动注入，无需交互选择）")
    else:
        child_env.pop("ELRS_UNIFIED_CONFIG", None)
        on_line("未选择硬件配置，生成 bare 固件（之后可用 Web UI 配置）")
    on_line(f"开始编译 {env}（首次编译需联网下载工具链，可能耗时较长）...")
    command = [python, "-m", "platformio", "run", "-e", env]
    attempts = 3
    for attempt in range(1, attempts + 1):
        code, output = run_capture(
            command, cwd=workdir, on_line=on_line,
            env=child_env, stdin=subprocess.PIPE,
        )
        if code == 0 or attempt == attempts or not any(
            marker in output for marker in FILE_LOCK_MARKERS
        ):
            break
        on_line(f"检测到固件文件被占用，2 秒后自动重试（{attempt}/{attempts - 1}）...")
        time.sleep(2)
    build_dir = artifacts_dir(source, env)
    artifacts = sorted(
        path for path in build_dir.glob("firmware.*")
        if path.suffix in {".bin", ".elrs", ".hex"}
    ) if build_dir is not None and build_dir.is_dir() else []
    on_line("OK   编译成功。" if code == 0 else f"失败：pio 退出码 {code}")
    return code == 0, artifacts


def read_hardware_catalog(
    source: Path, envs_by_kind: dict[str, list[str]] | None = None
) -> dict[str, dict[str, dict]]:
    """Vendor -> product -> build info from ExpressLRS hardware/targets.json.

    Each product carries the matching PlatformIO environments; an empty list
    means the checked-out ELRS version no longer ships that legacy target.
    """
    workdir = find_source(source)
    if not workdir:
        return {}
    path = workdir / "hardware" / "targets.json"
    if not path.is_file():
        path = workdir.parent / "hardware" / "targets.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    groups = envs_by_kind if envs_by_kind is not None else list_envs(source)
    all_envs = sorted({env for items in groups.values() for env in items})
    catalog: dict[str, dict[str, dict]] = {}
    for brand, info in data.items():
        if not isinstance(info, dict):
            continue
        vendor = str(info.get("name", brand))
        for section, items in info.items():
            if not isinstance(items, dict):
                continue
            for key, product in items.items():
                if not isinstance(product, dict) or "product_name" not in product:
                    continue
                firmware = str(product.get("firmware", ""))
                matched = sorted(
                    env for env in all_envs
                    if env == firmware or env.startswith(firmware + "_via_")
                )
                name = str(product["product_name"])
                label = name
                products = catalog.setdefault(vendor, {})
                if label in products:
                    label = f"{name} [{section}/{key}]"
                products[label] = {
                    "product_name": name,
                    "config": f"{brand}.{section}.{key}",
                    "firmware": firmware,
                    "kind": "TX" if firmware.endswith("_TX") or section.startswith("tx_") else "RX",
                    "layout": str(product.get("layout_file", "")),
                    "upload_methods": list(product.get("upload_methods", [])),
                    "platform": str(product.get("platform", "")),
                    "envs": matched,
                }
    return dict(sorted(catalog.items(), key=lambda item: item[0].upper()))


def read_layout_config(source: Path, config_path: str) -> dict:
    """S1: effective hardware layout for one product (layout file + overlay)."""
    result = {
        "product_name": "", "lua_name": "", "layout_file": "", "layout_path": "",
        "entries": [], "warning": "",
    }
    workdir = find_source(source)
    if not workdir:
        result["warning"] = "未找到 platformio.ini"
        return result
    targets_path = workdir / "hardware" / "targets.json"
    if not targets_path.is_file():
        targets_path = workdir.parent / "hardware" / "targets.json"
    if not targets_path.is_file():
        result["warning"] = "未找到 hardware/targets.json"
        return result
    try:
        data = json.loads(targets_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["warning"] = str(exc)
        return result
    node = data
    for part in config_path.split(".") if config_path else []:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            result["warning"] = f"未找到配置路径: {config_path}"
            return result
    if not isinstance(node, dict):
        result["warning"] = f"配置路径无效: {config_path}"
        return result
    result["product_name"] = str(node.get("product_name", ""))
    result["lua_name"] = str(node.get("lua_name", ""))
    layout_file = str(node.get("layout_file", ""))
    result["layout_file"] = layout_file
    if not layout_file:
        result["warning"] = "该型号没有 layout 文件（旧款专属 target）"
        return result
    folder = "TX" if "_TX" in str(node.get("firmware", "")) else "RX"
    layout_path = targets_path.parent / folder / layout_file
    result["layout_path"] = str(layout_path)
    try:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["warning"] = str(exc)
        return result
    entries = {
        key: {"key": key, "type": type(value).__name__,
              "value": value, "source": "layout"}
        for key, value in layout.items()
    }
    for key, value in (node.get("overlay") or {}).items():
        source = "overlay" if key in entries else "overlay (新增)"
        entries[key] = {
            "key": key, "type": type(value).__name__,
            "value": value, "source": source,
        }
    result["entries"] = sorted(entries.values(), key=lambda item: item["key"])
    return result


CONFIG_HEADER_SIZE = 128 + 16 + 512
CONFIG_LAYOUT_SIZE = 2048


def firmware_layout_offset(stream) -> int:
    """Offset of the 2048-byte layout area in an ESP firmware image."""
    stream.seek(0)
    magic, segments, *_ = struct.unpack("<BBBBI", stream.read(8))
    if magic != 0xE9:
        raise ValueError("不是有效的 ESP 固件（magic 不匹配）")
    is8285 = False
    if segments == 2:
        stream.seek(0x1000)
        _magic, segments, *_ = struct.unpack("<BBBBI", stream.read(8))
        is8285 = True
    else:
        stream.seek(24)
    for _ in range(segments):
        _address, size = struct.unpack("<II", stream.read(8))
        stream.seek(size, 1)
    position = (stream.tell() + 16) & ~15
    if not is8285:
        position += 32
    return position + CONFIG_HEADER_SIZE


def encode_layout(values: dict) -> bytes:
    return json.dumps(values).encode()


def parse_layout_input(text: str, original) -> object:
    """Parse a table cell back into the original JSON type."""
    raw = text.strip()
    if isinstance(original, bool):
        if raw.lower() in ("true", "1", "yes", "on"):
            return True
        if raw.lower() in ("false", "0", "no", "off"):
            return False
        raise ValueError("布尔值请输入 true / false")
    if isinstance(original, int):
        return int(raw)
    if isinstance(original, float):
        return float(raw)
    if isinstance(original, list):
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        sample = original[0] if original else 0
        if isinstance(sample, bool):
            return [part.lower() in ("true", "1", "yes", "on") for part in parts]
        if isinstance(sample, float):
            return [float(part) for part in parts]
        if isinstance(sample, int):
            return [int(part) for part in parts]
        return parts
    return text


def validate_layout(values: dict) -> tuple[bool, str]:
    try:
        data = encode_layout(values)
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    if len(data) > CONFIG_LAYOUT_SIZE:
        return False, f"配置序列化后 {len(data)} 字节，超过 ELRS 限制 {CONFIG_LAYOUT_SIZE} 字节"
    return True, ""


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")[:80] or "custom"


def validate_device_name(name: str) -> tuple[bool, str]:
    size = len(name.encode("utf-8"))
    if size >= 16:
        return False, f"设备名 {size} 字节，超过 ELRS 的 16 字节上限（建议英文数字不超过 15 个字符）"
    return True, ""


def set_firmware_device_name(stream, name: str) -> None:
    """S4: overwrite the 16-byte Lua device-name area of a firmware copy."""
    offset = firmware_layout_offset(stream) - CONFIG_HEADER_SIZE + 128
    stream.seek(offset)
    stream.write((name.encode("utf-8") + b"\0" * 16)[:16])


def write_layout_override(
    source: Path, env: str, product_name: str, values: dict,
    device_name: str | None = None,
) -> dict:
    """S2/S4: write config and firmware copies, never touching originals."""
    result = {"json": None, "bin": None, "bytes": 0, "warning": "",
              "device": device_name or ""}
    ok, message = validate_layout(values)
    if not ok:
        result["warning"] = message
        return result
    if device_name:
        ok, message = validate_device_name(device_name)
        if not ok:
            result["warning"] = message
            return result
    data = encode_layout(values)
    overrides = app_settings.overrides_dir()
    overrides.mkdir(parents=True, exist_ok=True)
    json_path = overrides / f"{_slug(product_name)}.layout.json"
    json_path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result["json"] = str(json_path)
    result["bytes"] = len(data)
    build = artifacts_dir(source, env)
    firmware = build / "firmware.bin" if build else None
    if not firmware or not firmware.is_file():
        result["warning"] = "尚未找到已编译的 firmware.bin，只生成了配置副本；编译后可再次保存生成固件副本。"
        return result
    output = app_settings.output_dir()
    output.mkdir(parents=True, exist_ok=True)
    custom = output / f"{_slug(product_name)}.{_slug(env)}.custom.bin"
    shutil.copy2(firmware, custom)
    with custom.open("r+b") as stream:
        stream.seek(firmware_layout_offset(stream))
        stream.write(data + b"\0" * (CONFIG_LAYOUT_SIZE - len(data)))
        if device_name:
            set_firmware_device_name(stream, device_name)
    result["bin"] = str(custom)
    return result


def to_tx_env(env: str) -> str:
    """RX-as-TX: use the matching TX firmware for the same upload method."""
    return re.sub(r"_RX(?=_via_|$)", "_TX", env)


def rx_as_tx_values(values: dict) -> dict:
    """Internal (full-duplex) RX-as-TX: keep two serial pins, rename led."""
    result = dict(values)
    if "led" in result and "led_red" not in result:
        result["led_red"] = result.pop("led")
    return result


def apply_active_layout(source: Path, env: str, config_path: str) -> dict | None:
    """S3: regenerate the firmware copy using the active config override."""
    active = app_settings.active_layout()
    if not active or active.get("config") != config_path:
        return None
    json_path = Path(str(active.get("json", "")))
    if not json_path.is_file():
        return {"json": None, "bin": None, "bytes": 0,
                "warning": "活动配置副本文件不存在，请重新保存配置。"}
    try:
        values = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"json": None, "bin": None, "bytes": 0, "warning": str(exc)}
    if active.get("rx_as_tx"):
        values = rx_as_tx_values(values)
        env = to_tx_env(env)
    return write_layout_override(
        source, env, str(active.get("product", "custom")), values,
        active.get("device_name"),
    )


PLATFORM_GROUPS = {
    "ESP32": {
        "prefix": "Unified_ESP32_", "platform": "espressif32",
        "tools": ("framework-arduinoespressif32", "toolchain-xtensa-esp32", "tool-esptoolpy"),
    },
    "ESP32-C3": {
        "prefix": "Unified_ESP32C3_", "platform": "espressif32",
        "tools": ("framework-arduinoespressif32", "toolchain-riscv32-esp", "tool-esptoolpy"),
    },
    "ESP32-S3": {
        "prefix": "Unified_ESP32S3_", "platform": "espressif32",
        "tools": ("framework-arduinoespressif32", "toolchain-xtensa-esp32s3", "tool-esptoolpy"),
    },
    "ESP8285": {
        "prefix": "Unified_ESP8285_", "platform": "espressif8266",
        "tools": ("framework-arduinoespressif8266", "toolchain-xtensa", "tool-esptoolpy"),
    },
}


def platform_package_status(source: Path) -> dict:
    """Check whether each chip family's PlatformIO packages are ready."""
    result = {"available": False, "groups": [], "missing": []}
    workdir = find_source(source)
    python = pio_python()
    if not workdir or not python or not pio_version(python):
        return result
    code, output = run_capture(
        [python, "-m", "platformio", "platform", "list", "--json-output"],
        cwd=workdir, env=pio_env(),
    )
    platforms = set()
    if code == 0:
        try:
            platforms = {item["name"] for item in json.loads(output)}
        except (ValueError, KeyError, TypeError):
            pass
    _code, tools_output = run_capture(
        [python, "-m", "platformio", "pkg", "list", "-g"],
        cwd=workdir, env=pio_env(),
    )
    installed_tools = set()
    for line in tools_output.splitlines():
        match = re.search(r"[├└]──\s+([^\s@]+)\s+@", line)
        if match:
            installed_tools.add(match.group(1))
    envs = [env for items in list_envs(source).values() for env in items]
    result["available"] = True
    for group, spec in PLATFORM_GROUPS.items():
        candidates = sorted(env for env in envs if env.startswith(spec["prefix"]))
        if not candidates:
            continue
        env = (next((item for item in candidates if "_TX_via_UART" in item), None)
               or next((item for item in candidates if "_via_UART" in item), candidates[0]))
        missing_tools = [tool for tool in spec["tools"] if tool not in installed_tools]
        libs_ready = (workdir / ".pio" / "libdeps" / env).is_dir()
        entry = {
            "group": group, "env": env, "platform": spec["platform"],
            "platform_ok": spec["platform"] in platforms,
            "missing_tools": missing_tools, "libs_ok": libs_ready,
        }
        entry["ready"] = (
            entry["platform_ok"] and not missing_tools and libs_ready
        )
        result["groups"].append(entry)
    result["missing"] = [entry for entry in result["groups"] if not entry["ready"]]
    return result


def check_platform_packages(source: Path, on_line, on_warning=None) -> dict:
    warn = on_warning or on_line
    status = platform_package_status(source)
    if not status["available"]:
        on_line("跳过平台包检查：PlatformIO 不可用，请先安装。")
        return status
    workdir = find_source(source)
    on_line(f"平台包检查（全局: {app_settings.pio_core_dir() or 'PlatformIO 默认目录'}；项目库: {workdir / '.pio' / 'libdeps'}）")
    for entry in status["groups"]:
        details = []
        details.append("平台 OK" if entry["platform_ok"] else f"平台缺失 {entry['platform']}")
        if entry["missing_tools"]:
            details.append("缺工具包 " + ", ".join(entry["missing_tools"]))
        else:
            details.append("工具包 OK")
        details.append("代表目标依赖库已装" if entry["libs_ok"] else "代表目标依赖库未装")
        line = f"{'OK' if entry['ready'] else '缺失':<4} {entry['group']}: " + " | ".join(details)
        (on_line if entry["ready"] else warn)(line)
    if status["missing"]:
        on_line("提示：可点击「预下载平台包」提前安装，之后首次编译会快很多。")
    else:
        on_line("全部平台包已就绪。")
    return status


def prefetch_platform_packages(source: Path, groups: list[dict], on_line) -> bool:
    """Install platform/tool/library deps without compiling firmware."""
    workdir = find_source(source)
    python = pio_python()
    if not workdir or not python or not pio_version(python):
        on_line("失败：PlatformIO 不可用。")
        return False
    ok = True
    for entry in groups:
        on_line(f"正在预装 {entry['group']}（{entry['env']}）...")
        code, _ = run_capture(
            [python, "-m", "platformio", "pkg", "install", "-e", entry["env"]],
            cwd=workdir, on_line=on_line, env=pio_env(), stdin=subprocess.PIPE,
        )
        if code != 0:
            on_line(f"失败：{entry['group']} 预装未完成。")
            ok = False
    return ok


REGULATORY_DOMAINS = (
    "Regulatory_Domain_AU_915", "Regulatory_Domain_EU_868",
    "Regulatory_Domain_IN_866", "Regulatory_Domain_AU_433",
    "Regulatory_Domain_EU_433", "Regulatory_Domain_US_433",
    "Regulatory_Domain_US_433_WIDE", "Regulatory_Domain_FCC_915",
    "Regulatory_Domain_TH_920", "Regulatory_Domain_ISM_2400",
    "Regulatory_Domain_EU_CE_2400",
)


def user_defines_path(source: Path) -> Path | None:
    workdir = find_source(source)
    if not workdir:
        return None
    path = workdir / "user_defines.txt"
    return path if path.is_file() else None


def read_user_defines(source: Path) -> dict[str, str | bool]:
    """Read the common user_defines options without touching other lines."""
    values: dict[str, str | bool] = {"domain": "默认", "binding": "", "lock": False}
    path = user_defines_path(source)
    if not path:
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*(#?)\s*-D(\w+)(?:=(.*?))?\s*$", line)
        if not match:
            continue
        commented, name, value = match.group(1), match.group(2), match.group(3) or ""
        if commented:
            continue
        if name.startswith("Regulatory_Domain_"):
            values["domain"] = name
        elif name == "MY_BINDING_PHRASE":
            values["binding"] = value.strip('"')
        elif name == "LOCK_ON_FIRST_CONNECTION":
            values["lock"] = True
    return values


def write_user_defines(source: Path, values: dict[str, str | bool]) -> tuple[bool, str]:
    """Precisely toggle the common -D lines; keep every other line untouched."""
    path = user_defines_path(source)
    if not path:
        return False, "未找到 user_defines.txt"
    domain = str(values.get("domain", "默认"))
    binding = str(values.get("binding", "")).strip().replace('"', "")
    lock = bool(values.get("lock", False))
    target_names = set(REGULATORY_DOMAINS) | {"MY_BINDING_PHRASE", "LOCK_ON_FIRST_CONNECTION"}
    raw_lines = path.read_bytes().splitlines(keepends=True)
    found: set[str] = set()
    updated: list[bytes] = []
    for raw in raw_lines:
        eol = b"\r\n" if raw.endswith(b"\r\n") else (b"\n" if raw.endswith(b"\n") else b"")
        body = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
        match = re.match(r"^(\s*)(#?)\s*-D(\w+)(?:=(.*?))?\s*$", body)
        if not match or match.group(3) not in target_names:
            updated.append(raw)
            continue
        indent, _commented, name, _value = match.groups()
        found.add(name)
        if name.startswith("Regulatory_Domain_"):
            enabled = name == domain
            text = f"{indent}{'' if enabled else '#'}-D{name}"
        elif name == "MY_BINDING_PHRASE":
            if binding:
                text = f'{indent}-DMY_BINDING_PHRASE="{binding}"'
            else:
                text = f"{indent}#-DMY_BINDING_PHRASE={_value or ''}"
        else:
            text = f"{indent}{'' if lock else '#'}-D{name}"
        updated.append(text.encode("utf-8") + eol)
    missing = sorted(target_names - found)
    backup = path.with_suffix(".txt.bak")
    if not backup.is_file():
        backup.write_bytes(path.read_bytes())
    path.write_bytes(b"".join(updated))
    note = f"备份: {backup}"
    if missing:
        note += "；文件中缺少这些定义（未新增）: " + ", ".join(missing)
    return True, note


def main() -> int:
    parser = argparse.ArgumentParser(description="ExpressLRS PlatformIO 后端")
    parser.add_argument("command", choices=(
        "check", "install", "list", "build", "defines", "hardware",
        "layout", "packages", "prefetch",
    ))
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--env", default="")
    parser.add_argument("--domain", choices=("默认", *REGULATORY_DOMAINS))
    parser.add_argument("--binding")
    parser.add_argument("--lock", choices=("on", "off"))
    parser.add_argument("--vendor")
    parser.add_argument("--config", help="非交互硬件配置键路径，如 radiomaster.tx_2400.boxer")
    args = parser.parse_args()
    source = Path(args.source)

    def on_line(line: str):
        print(line, flush=True)

    if args.command in ("packages", "prefetch"):
        status = check_platform_packages(source, on_line)
        if not status["available"]:
            return 1
        if args.command == "prefetch" and status["missing"]:
            return 0 if prefetch_platform_packages(source, status["missing"], on_line) else 1
        return 0
    if args.command == "check":
        return 0 if check_environment(source, on_line) else 1
    if args.command == "install":
        return 0 if install_platformio(on_line) else 1
    if args.command == "list":
        groups = list_envs(source)
        if not groups:
            print("未读取到编译目标，请检查源码目录与 PlatformIO 安装。")
            return 1
        for kind, envs in groups.items():
            print(f"{kind} ({len(envs)}):")
            for env in envs:
                print(f"  {env}")
        return 0
    if args.command == "hardware":
        catalog = read_hardware_catalog(source)
        if not catalog:
            print("未找到 hardware/targets.json。")
            return 1
        products = [item for items in catalog.values() for item in items.values()]
        buildable = [item for item in products if item["envs"]]
        print(f"厂商 {len(catalog)} 家，型号 {len(products)} 个，可编译 {len(buildable)} 个")
        vendors = [args.vendor] if args.vendor else list(catalog)
        for vendor in vendors:
            items = catalog.get(vendor)
            if not items:
                print(f"未找到厂商: {vendor}")
                continue
            print(f"\n{vendor} ({len(items)}):")
            for label, item in sorted(items.items()):
                envs = ", ".join(item["envs"]) if item["envs"] else "当前源码无对应编译目标"
                print(f"  {label}  ->  {envs}")
        return 0
    if args.command == "layout":
        if not args.config:
            print("layout 需要 --config <键路径>，如 radiomaster.tx_2400.boxer")
            return 1
        info = read_layout_config(source, args.config)
        print(f"型号: {info['product_name']}")
        print(f"layout: {info['layout_file']}")
        print(f"条目: {len(info['entries'])}")
        if info["warning"]:
            print(f"提示: {info['warning']}")
        for entry in info["entries"]:
            print(f"  {entry['key']:<24} {entry['type']:<6} "
                  f"{json.dumps(entry['value'], ensure_ascii=False):<28} {entry['source']}")
        return 0
    if args.command == "defines":
        values = read_user_defines(source)
        changed = any(value is not None for value in (args.domain, args.binding, args.lock))
        if changed:
            if args.domain is not None:
                values["domain"] = args.domain
            if args.binding is not None:
                values["binding"] = args.binding
            if args.lock is not None:
                values["lock"] = args.lock == "on"
            ok, note = write_user_defines(source, values)
            print(note)
            if not ok:
                return 1
            values = read_user_defines(source)
        print(f"domain  = {values['domain']}")
        print(f"binding = {values['binding']}")
        print(f"lock    = {'on' if values['lock'] else 'off'}")
        return 0
    if not args.env:
        print("build 需要 --env <目标名>")
        return 1
    ok, artifacts = build_firmware(source, args.env, on_line, args.config)
    for artifact in artifacts:
        print(f"产物: {artifact}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
