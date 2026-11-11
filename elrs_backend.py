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
import re
import sys
from pathlib import Path

from builder_utils import external_python, run_capture

DEFAULT_SOURCE = r"E:\Code\ExpressLRS"


def venv_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base / "tools" / "pio-venv"


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
    code, output = run_capture([python, "-m", "platformio", "--version"])
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
        cwd=workdir,
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


def build_firmware(source: Path, env: str, on_line) -> tuple[bool, list[Path]]:
    workdir = find_source(source)
    python = pio_python()
    if not workdir:
        on_line("失败：未找到 platformio.ini。")
        return False, []
    if not python or not pio_version(python):
        on_line("失败：PlatformIO 不可用，请先安装。")
        return False, []
    on_line(f"开始编译 {env}（首次编译需联网下载工具链，可能耗时较长）...")
    code, _ = run_capture(
        [python, "-m", "platformio", "run", "-e", env], cwd=workdir, on_line=on_line
    )
    build_dir = workdir / ".pio" / "build" / env
    artifacts = sorted(
        path for path in build_dir.glob("firmware.*")
        if path.suffix in {".bin", ".elrs", ".hex"}
    ) if build_dir.is_dir() else []
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
                    "firmware": firmware,
                    "kind": "TX" if firmware.endswith("_TX") or section.startswith("tx_") else "RX",
                    "layout": str(product.get("layout_file", "")),
                    "upload_methods": list(product.get("upload_methods", [])),
                    "platform": str(product.get("platform", "")),
                    "envs": matched,
                }
    return dict(sorted(catalog.items(), key=lambda item: item[0].upper()))


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
    parser.add_argument("command", choices=("check", "install", "list", "build", "defines", "hardware"))
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--env", default="")
    parser.add_argument("--domain", choices=("默认", *REGULATORY_DOMAINS))
    parser.add_argument("--binding")
    parser.add_argument("--lock", choices=("on", "off"))
    parser.add_argument("--vendor")
    args = parser.parse_args()
    source = Path(args.source)

    def on_line(line: str):
        print(line, flush=True)

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
    ok, artifacts = build_firmware(source, args.env, on_line)
    for artifact in artifacts:
        print(f"产物: {artifact}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
