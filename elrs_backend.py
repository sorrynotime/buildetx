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


def main() -> int:
    parser = argparse.ArgumentParser(description="ExpressLRS PlatformIO 后端")
    parser.add_argument("command", choices=("check", "install", "list", "build"))
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--env", default="")
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
    if not args.env:
        print("build 需要 --env <目标名>")
        return 1
    ok, artifacts = build_firmware(source, args.env, on_line)
    for artifact in artifacts:
        print(f"产物: {artifact}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
