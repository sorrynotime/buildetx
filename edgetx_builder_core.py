"""EdgeTX source inspection and process helpers used by the GUI."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path


def clang_version(bin_dir: Path) -> int | None:
    clang = bin_dir / "clang.exe"
    if not clang.is_file():
        return None
    code, output = run_capture([str(clang), "--version"])
    match = re.search(r"clang version (\d+)", output, re.IGNORECASE)
    return int(match.group(1)) if code == 0 and match else None


def find_compatible_libclang() -> tuple[Path | None, int | None]:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates = []
    vswhere_paths = (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
        program_files / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
    )
    for vswhere in vswhere_paths:
        if not vswhere.is_file():
            continue
        code, output = run_capture([str(vswhere), "-products", "*", "-property", "installationPath"])
        if code == 0:
            candidates.extend(Path(line.strip()) / "VC" / "Tools" / "Llvm" / "x64" / "bin"
                              for line in output.splitlines() if line.strip())
        break
    vs_install = os.environ.get("VSINSTALLDIR")
    if vs_install:
        candidates.append(Path(vs_install) / "VC" / "Tools" / "Llvm" / "x64" / "bin")
    root = program_files / "Microsoft Visual Studio"
    if root.is_dir():
        candidates.extend(root.glob("*/*/VC/Tools/Llvm/x64/bin"))
    compatible = []
    for candidate in candidates:
        if not (candidate / "libclang.dll").is_file():
            continue
        version = clang_version(candidate)
        if version is not None and version >= 20:
            compatible.append((version, candidate))
    return max(compatible, key=lambda item: item[0])[::-1] if compatible else (None, None)


def path_libclang() -> tuple[Path | None, int | None]:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(entry)
        if (path / "libclang.dll").is_file():
            return path, clang_version(path)
    return None, None


def read_cmake_options(source: Path, target: str) -> dict[str, str]:
    paths = [source / "radio" / "src" / "CMakeLists.txt",
             source / "radio" / "src" / "targets" / "common" / "arm" / "CMakeLists.txt",
             source / "radio" / "src" / "targets" / target / "CMakeLists.txt"]
    defaults: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith("#"):
                    continue
                code_line = line.split(" #", 1)[0]
                option = re.search(r"\boption\((\w+)\s+.*\s+(ON|OFF)\)\s*$", code_line, re.I)
                assignment = re.search(r'\bset\((\w+)\s+"?([A-Za-z0-9_+-]+)', code_line, re.I)
                match = option or assignment
                if match:
                    key, value = match.group(1).upper(), match.group(2).upper()
                    defaults[key] = {"YES": "ON", "NO": "OFF"}.get(value, value)
        except OSError:
            pass
    return defaults


def read_target_defaults(source: Path, target: str) -> dict[str, str]:
    options = read_cmake_options(source, target)
    return {key: options[key] for key in ("FLYSKY_GIMBAL", "BLUETOOTH", "INTERNAL_GPS", "STICK_DEAD_ZONE", "STICKS") if key in options}


def read_target_capabilities(source: Path, target: str) -> set[str]:
    return set(read_cmake_options(source, target))


def _decode(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return raw.decode("gb18030", errors="replace") if "�" in text else text


def run_capture(args: list[str], cwd: Path | None = None, on_line=None) -> tuple[int, str]:
    try:
        if on_line is None:
            p = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            return p.returncode, _decode(p.stdout or b"").strip()
        p = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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


def read_literal_boards(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "boards" for t in node.targets):
                value = ast.literal_eval(node.value)
                return {str(k): {str(a): str(b) for a, b in v.items()} for k, v in value.items()}
    except (OSError, SyntaxError, ValueError):
        pass
    return {}


def read_common_targets(source: Path) -> dict[str, dict[str, str]]:
    path = source / "tools" / "build-common.sh"
    if not path.is_file():
        return {}
    targets, target = {}, ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*([a-z0-9+_-]+)\)\s*$", line)
            if match:
                target = match.group(1)
                continue
            options = re.search(r'BUILD_OPTIONS\+="([^"]+)"', line)
            if target and options:
                config = dict(re.findall(r"-D([^=\s]+)=([^\s]+)", options.group(1)))
                if config:
                    targets[target] = config
    except OSError:
        pass
    return targets


def read_target_catalog(source: Path) -> dict[str, dict[str, dict[str, str]]]:
    targets = read_common_targets(source) or {name.lower(): config for name, config in read_literal_boards(source / "tools" / "boards.py").items()}
    labels: dict[str, str] = {}
    try:
        data = json.loads((source / "fw.json").read_text(encoding="utf-8"))
        for label, prefix in data.get("targets", []):
            labels[str(prefix).rstrip("-")] = str(label)
    except (OSError, ValueError, TypeError):
        pass
    brands = {"x9lite":"FrSky", "x9lites":"FrSky", "x7":"FrSky", "x7access":"FrSky", "xlite":"FrSky", "xlites":"FrSky", "x9d":"FrSky", "x9dp":"FrSky", "x9dp2019":"FrSky", "x9e":"FrSky", "x9e-hall":"FrSky", "x10":"FrSky", "x10express":"FrSky", "x12s":"FrSky", "t12":"Jumper", "tlite":"Jumper", "tpro":"Jumper", "tprov2":"Jumper", "t12max":"Jumper", "t14":"Jumper", "t16":"Jumper", "t18":"Jumper", "tpros":"Jumper", "bumblebee":"Jumper", "t8":"RadioMaster", "tx12":"RadioMaster", "tx12mk2":"RadioMaster", "zorro":"RadioMaster", "boxer":"RadioMaster", "pocket":"RadioMaster", "mt12":"RadioMaster", "gx12":"RadioMaster", "lr3pro":"BetaFPV", "commando8":"iFlight"}
    catalog: dict[str, dict[str, dict[str, str]]] = {}
    for target, config in targets.items():
        label = labels.get(target, target.upper())
        brand = {"Flysky": "FlySky"}.get(label.split(maxsplit=1)[0], label.split(maxsplit=1)[0])
        if brand not in {"FrSky", "Jumper", "RadioMaster", "FlySky", "iFlight", "BetaFPV", "HelloRadioSky", "Fatfish"}:
            brand = brands.get(target, "其他")
        catalog.setdefault(brand, {})[label] = {"_target": target, **config}
    return catalog
