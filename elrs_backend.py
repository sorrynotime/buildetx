"""ExpressLRS build backend (PlatformIO).

Interface is frozen in M1 so the UI can be built against it; the real
implementation lands in M2. No GUI imports here: the same functions are
used by the Tkinter app and by the command line interface.

Callbacks: on_line(text) receives one log line at a time.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_SOURCE = r"E:\Code\ExpressLRS"


def find_source(source: Path) -> Path | None:
    """Return the PlatformIO working directory for an ELRS checkout.

    Recent ExpressLRS trees keep platformio.ini in src/; older ones at root.
    """
    for candidate in (source / "src", source):
        if (candidate / "platformio.ini").is_file():
            return candidate
    return None


def check_environment(source: Path, on_line) -> bool:
    """Report python/git/PlatformIO status. M2: real checks."""
    on_line("ExpressLRS 环境检查（界面预览）：后端将在下一步接入。")
    on_line(f"源码目录: {source}")
    workdir = find_source(source)
    if workdir:
        on_line(f"OK   检测到 platformio.ini: {workdir}")
    else:
        on_line("缺失 未找到 platformio.ini（应位于源码根目录或 src 子目录）。")
    on_line("未完成 PlatformIO 检测：等待 M2 实现。")
    return False


def install_platformio(on_line) -> bool:
    """Create a dedicated venv and install PlatformIO. M2: real install."""
    on_line("安装 PlatformIO（界面预览）：后端将在下一步接入，当前未执行任何安装。")
    return False


def list_envs(source: Path) -> dict[str, list[str]]:
    """Return build environments grouped by TX/RX. M2: pio project config."""
    workdir = find_source(source)
    if not workdir:
        return {}
    return {
        "TX": ["DEMO_ESP32_2400_TX", "DEMO_ESP32S3_900_TX"],
        "RX": ["DEMO_ESP32_2400_RX", "DEMO_ESP8285_2400_RX"],
    }


def build_firmware(source: Path, env: str, on_line) -> tuple[bool, list[Path]]:
    """Build one environment and return (ok, artifacts). M2: pio run."""
    on_line(f"编译 ExpressLRS（界面预览）：目标 {env}，后端将在下一步接入，未执行编译。")
    return False, []
