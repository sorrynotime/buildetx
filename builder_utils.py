"""Shared process and resource helpers for Open Radio Builder backends."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _decode(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return raw.decode("gb18030", errors="replace") if "�" in text else text


def _subprocess_creationflags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def run_capture(
    args: list[str], cwd: Path | None = None, on_line=None,
    env: dict[str, str] | None = None, stdin=None,
) -> tuple[int, str]:
    try:
        creationflags = _subprocess_creationflags()
        if on_line is None:
            p = subprocess.run(
                args, cwd=cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=creationflags,
                env=env, stdin=stdin,
            )
            return p.returncode, _decode(p.stdout or b"").strip()
        p = subprocess.Popen(
            args, cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=creationflags,
            env=env, stdin=stdin,
        )
        if p.stdin is not None:
            p.stdin.close()
        lines = []
        assert p.stdout is not None
        for raw_line in iter(p.stdout.readline, b""):
            line = _decode(raw_line).rstrip("\r\n")
            lines.append(line)
            on_line(line)
        p.wait()
        return p.returncode, "\n".join(lines).strip()
    except OSError as exc:
        return 1, str(exc)


def external_python() -> str | None:
    return shutil.which("python")


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name
