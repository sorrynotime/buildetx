"""EdgeTX Builder for Windows.

Single-file application for configuring and building EdgeTX targets.
"""

from __future__ import annotations

import ast
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


# Source inspection and process helpers.
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


# GUI application.

TOOLS = ("cmake", "ninja", "arm-none-eabi-gcc", "python", "git")
REQUIRED_IMPORTS = {"PIL": "Pillow", "lz4.block": "lz4"}
DEFAULT_SOURCE = r"E:\Code\edgetx"
DEFAULT_VS_SHELL = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Visual Studio\Visual Studio Tools\Developer PowerShell for VS.lnk"
LANGUAGES = (
    "CN", "CZ", "DA", "DE", "EN", "ES", "FI", "FR", "HE", "HU", "IT",
    "JP", "KO", "NL", "PL", "PT", "RU", "SE", "SK", "TW", "UA",
)
COMMON_FEATURES = (
    ("HELI", "直升机菜单", True),
    ("FLIGHT_MODES", "飞行模式", True),
    ("CURVES", "曲线", True),
    ("GVARS", "全局变量", True),
    ("LUA", "Lua 脚本", True),
    ("LUA_MIXER", "Lua 混控脚本", True),
    ("BOOTLOADER", "内置 Bootloader", True),
    ("EXFAT", "exFAT 文件系统", False),
)
PROTOCOL_OPTIONS = (
    ("MODULE_PROTOCOL_FCC", "FCC"),
    ("MODULE_PROTOCOL_LBT", "LBT"),
    ("MODULE_PROTOCOL_D8", "D8"),
)
MODULE_OPTIONS = (
    ("XJT", "XJT"),
    ("PPM", "PPM"),
    ("DSM2", "DSM2"),
    ("DSMP", "DSMP"),
    ("SBUS", "SBUS"),
    ("CROSSFIRE", "Crossfire"),
    ("AFHDS2", "AFHDS2"),
    ("AFHDS3", "AFHDS3"),
    ("MULTIMODULE", "MultiModule"),
    ("GHOST", "Ghost"),
)
HARDWARE_BOOLEAN_OPTIONS = (
    ("FLYSKY_GIMBAL", "FS 霍尔摇杆"),
    ("BLUETOOTH", "蓝牙模块"),
    ("INTERNAL_GPS", "内置 GPS"),
    ("STICK_DEAD_ZONE", "摇杆死区"),
)
HARDWARE_CHOICES = {
    "默认": None,
    "开启": "YES",
    "关闭": "NO",
}
MISSING_TOOL_HELP = {
    "cmake": "请安装 CMake，并将其 bin 目录添加到 PATH 后重启本程序。",
    "ninja": "请安装 Ninja，或将 ninja.exe 所在目录添加到 PATH 后重启本程序。",
    "arm-none-eabi-gcc": (
        "请安装 ARM GNU Toolchain，并将其 bin 目录添加到 PATH 后重启本程序。"
    ),
    "python": "请安装 Python，并勾选安装程序中的 Add Python to PATH 后重启本程序。",
    "git": "请安装 Git，或将 Git 的 cmd 目录添加到 PATH 后重启本程序。",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("EdgeTX Builder - Windows")
        self.root.geometry("980x856")
        self.root.resizable(False, False)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self.spinner_index = 0
        self.targets: dict[str, dict[str, dict[str, str]]] = {}

        self.source = tk.StringVar(value=DEFAULT_SOURCE)
        self.vs_shell = tk.StringVar(value=DEFAULT_VS_SHELL)
        self.brand = tk.StringVar(value="Jumper")
        self.target = tk.StringVar(value="")
        self.translation = tk.StringVar(value="CN")
        self.build_type = tk.StringVar(value="Release")
        self.default_mode = tk.StringVar(value="默认")
        self.build_dir = tk.StringVar(value="")
        self.status = tk.StringVar(value="请选择 EdgeTX 源码目录，然后点击检查环境")
        self.feature_vars = {
            option: tk.BooleanVar(value=default)
            for option, _, default in COMMON_FEATURES
        }
        self.hardware_vars = {
            option: tk.StringVar(value="默认")
            for option, _ in HARDWARE_BOOLEAN_OPTIONS
        }
        self.sticks_type = tk.StringVar(value="默认")
        self.hardware_defaults: dict[str, str] = {}
        self.hardware_warning = tk.StringVar(value="")
        self.protocol_vars = {
            option: tk.BooleanVar(value=True)
            for option, _ in PROTOCOL_OPTIONS
        }
        self.module_vars = {
            option: tk.BooleanVar(value=True)
            for option, _ in MODULE_OPTIONS
        }
        self.protocol_widgets: dict[str, ttk.Checkbutton] = {}
        self.module_widgets: dict[str, ttk.Checkbutton] = {}
        self.capabilities: set[str] = set()
        self.layout_vars: dict[str, tk.IntVar] = {
            "window_width": tk.IntVar(value=980),
            "window_height": tk.IntVar(value=856),
            "outer_padding": tk.IntVar(value=10),
            "column_gap": tk.IntVar(value=5),
            "left_weight": tk.IntVar(value=1),
            "right_weight": tk.IntVar(value=8),
            "panels_bottom_gap": tk.IntVar(value=10),
            "target_width": tk.IntVar(value=412),
            "target_height": tk.IntVar(value=298),
            "target_x": tk.IntVar(value=1),
            "target_y": tk.IntVar(value=1),
            "target_pad_x": tk.IntVar(value=1),
            "target_pad_y": tk.IntVar(value=1),
            "target_label_gap": tk.IntVar(value=1),
            "target_column_gap": tk.IntVar(value=6),
            "target_row_gap": tk.IntVar(value=6),
            "target_control_width": tk.IntVar(value=8),
            "target_combo_gap": tk.IntVar(value=10),
            "target_button_height": tk.IntVar(value=2),
            "feature_width": tk.IntVar(value=620),
            "feature_height": tk.IntVar(value=102),
            "feature_x": tk.IntVar(value=1),
            "feature_y": tk.IntVar(value=1),
            "feature_pad_x": tk.IntVar(value=1),
            "feature_pad_y": tk.IntVar(value=1),
            "hardware_width": tk.IntVar(value=960),
            "hardware_height": tk.IntVar(value=120),
            "hardware_x": tk.IntVar(value=1),
            "hardware_y": tk.IntVar(value=1),
            "hardware_pad_x": tk.IntVar(value=1),
            "hardware_pad_y": tk.IntVar(value=1),
            "hardware_label_gap": tk.IntVar(value=20),
            "hardware_row_gap": tk.IntVar(value=1),
            "hardware_columns": tk.IntVar(value=3),
            "protocol_width": tk.IntVar(value=620),
            "protocol_height": tk.IntVar(value=92),
            "protocol_x": tk.IntVar(value=1),
            "protocol_y": tk.IntVar(value=2),
            "module_width": tk.IntVar(value=620),
            "module_height": tk.IntVar(value=100),
            "module_x": tk.IntVar(value=1),
            "module_y": tk.IntVar(value=1),
            "support_gap": tk.IntVar(value=1),
            "support_pad_x": tk.IntVar(value=1),
            "support_pad_y": tk.IntVar(value=1),
            "support_columns": tk.IntVar(value=5),
            "support_item_gap": tk.IntVar(value=1),
            "support_row_gap": tk.IntVar(value=1),
            "button_config_gap": tk.IntVar(value=1),
            "button_row_pady": tk.IntVar(value=6),
            "button_item_padx": tk.IntVar(value=3),
            "build_width": tk.IntVar(value=960),
            "build_height": tk.IntVar(value=306),
            "build_x": tk.IntVar(value=1),
            "build_y": tk.IntVar(value=1),
            "build_pad_x": tk.IntVar(value=1),
            "build_pad_y": tk.IntVar(value=1),
            "log_height": tk.IntVar(value=12),
            "log_pad_x": tk.IntVar(value=1),
            "log_pad_y": tk.IntVar(value=1),
        }
        self.layout_debug_window: tk.Toplevel | None = None

        self._setup_theme()
        self._build_ui()
        for variable in self.layout_vars.values():
            variable.trace_add("write", self._layout_changed)
        self._apply_layout()
        self.root.after(150, self._drain_events)
        self.root.after(180, self.spinner_tick)

    def _setup_theme(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(background="#eaf4ff")
        style.configure("TFrame", background="#eaf4ff")
        style.configure("App.TFrame", background="#eaf4ff")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#eaf4ff", foreground="#243247",
                        font=("Segoe UI", 9))
        style.configure("App.TLabel", background="#eaf4ff", foreground="#243247",
                        font=("Segoe UI", 9))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#243247",
                        font=("Segoe UI", 9))
        style.configure("PanelTitle.TLabel", background="#ffffff", foreground="#102a43",
                        font=("Segoe UI Semibold", 10))
        style.configure(
            "TButton", padding=(15, 7), font=("Segoe UI Semibold", 9),
            background="#f8fbff", foreground="#27415d", borderwidth=1,
            bordercolor="#b8cce0", lightcolor="#ffffff", darkcolor="#b8cce0",
            relief="flat", focuscolor="#93c5fd",
        )
        style.map(
            "TButton",
            background=[("active", "#e6f1ff"), ("pressed", "#cfe4fb")],
            bordercolor=[("active", "#78aee8"), ("pressed", "#5e9cdd")],
            foreground=[("disabled", "#8a9aab"), ("pressed", "#1e3a5f")],
        )
        style.configure(
            "Primary.TButton", padding=(17, 8), foreground="#ffffff",
            background="#3478d4", borderwidth=1, bordercolor="#2865b8",
            lightcolor="#4f91e7", darkcolor="#2865b8", relief="flat",
            font=("Segoe UI Semibold", 9), focuscolor="#93c5fd",
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#286cc5"), ("pressed", "#205ca9")],
            bordercolor=[("active", "#205ca9"), ("pressed", "#184b8d")],
            foreground=[("disabled", "#d6e6fa")],
        )
        style.configure("TCheckbutton", background="#ffffff", foreground="#243247",
                        font=("Segoe UI", 9))
        style.configure("Panel.TCheckbutton", background="#ffffff",
                        foreground="#243247", font=("Segoe UI", 9))
        self._checkbox_empty = tk.PhotoImage(width=14, height=14)
        self._checkbox_checked = tk.PhotoImage(width=14, height=14)
        for image, fill in ((self._checkbox_empty, "#ffffff"),
                            (self._checkbox_checked, "#2563eb")):
            image.put(fill, to=(1, 1, 13, 13))
            image.put("#90a4b8", to=(0, 0, 14, 1))
            image.put("#90a4b8", to=(0, 13, 14, 14))
            image.put("#90a4b8", to=(0, 0, 1, 14))
            image.put("#90a4b8", to=(13, 0, 14, 14))
        for x, y in ((3, 7), (4, 8), (5, 9), (6, 8), (7, 7), (8, 6), (9, 5), (10, 4)):
            self._checkbox_checked.put("#ffffff", to=(x, y, x + 2, y + 2))
        style.element_create(
            "Builder.CheckIndicator", "image", self._checkbox_empty,
            ("selected", self._checkbox_checked), border=0, sticky="w",
        )
        check_layout = [
            ("Builder.CheckIndicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})
            ]}),
        ]
        style.layout("TCheckbutton", check_layout)
        style.layout("Panel.TCheckbutton", check_layout)
        style.configure("Panel.TRadiobutton", background="#ffffff",
                        foreground="#243247", font=("Segoe UI", 9))
        style.configure(
            "TEntry", fieldbackground="#ffffff", background="#ffffff",
            foreground="#243247", insertcolor="#243247",
        )
        style.configure(
            "TCombobox", padding=(5, 3), fieldbackground="#ffffff",
            background="#ffffff", foreground="#243247", arrowcolor="#486581",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#ffffff")],
            selectbackground=[("readonly", "#dbeafe")],
            selectforeground=[("readonly", "#243247")],
        )
        style.configure("TLabelframe", background="#eaf4ff",
                        bordercolor="#9bb7d4")
        style.configure("TLabelframe.Label", background="#eaf4ff",
                        foreground="#52637a", font=("Segoe UI Semibold", 9))
        style.configure(
            "TProgressbar", background="#60a5fa", troughcolor="#dbeafe",
            bordercolor="#b7cbe0", lightcolor="#60a5fa", darkcolor="#60a5fa",
        )

    def _make_rounded_panel(self, parent, title: str, height: int = 250):
        canvas = tk.Canvas(
            parent, height=height, background="#eaf4ff",
            highlightthickness=0, bd=0
        )
        inner = tk.Frame(canvas, background="#ffffff", padx=14, pady=10)
        window = canvas.create_window((14, 12), window=inner, anchor="nw")

        def redraw(event=None):
            width = max(canvas.winfo_width(), 220)
            panel_height = max(canvas.winfo_height(), 40)
            canvas.delete("panel-bg")
            radius = 12
            x1, y1, x2, y2 = 2, 2, width - 2, panel_height - 2
            canvas.create_rectangle(
                x1 + radius, y1, x2 - radius, y2,
                fill="#ffffff", outline="", tags="panel-bg"
            )
            canvas.create_rectangle(
                x1, y1 + radius, x2, y2 - radius,
                fill="#ffffff", outline="", tags="panel-bg"
            )
            for x, y, start in (
                (x1, y1, 90), (x2 - 2 * radius, y1, 0),
                (x1, y2 - 2 * radius, 180), (x2 - 2 * radius, y2 - 2 * radius, 270)
            ):
                canvas.create_arc(
                    x, y, x + 2 * radius, y + 2 * radius,
                    start=start, extent=90, fill="#ffffff",
                    outline="", tags="panel-bg"
                )
            canvas.tag_lower("panel-bg")
            canvas.itemconfigure(window, width=max(width - 28, 180))

        canvas.bind("<Configure>", redraw)
        return canvas, inner

    def _layout_changed(self, *_args):
        if hasattr(self, "outer"):
            self._apply_layout()

    def _layout_value(self, name: str, fallback: int) -> int:
        try:
            return max(1, int(self.layout_vars[name].get()))
        except (tk.TclError, ValueError):
            return fallback

    def _apply_layout(self, refresh=True):
        values = {
            name: self._layout_value(name, variable.get())
            for name, variable in self.layout_vars.items()
            if name != "window_width" and name != "window_height"
        }
        self.root.geometry("980x856")
        self.outer.configure(padding=values["outer_padding"])
        self.panels.columnconfigure(0, weight=values["left_weight"])
        self.panels.columnconfigure(1, weight=values["right_weight"])
        gap = values["column_gap"]
        self.left_column.grid_configure(padx=(0, gap))
        self.right_column.grid_configure(padx=(gap, 0))
        self.target_panel.configure(
            width=values["target_width"], height=values["target_height"]
        )
        self.target_panel.grid_configure(
            padx=(values["target_x"], 0), pady=(values["target_y"], 0)
        )
        self.target_frame.configure(
            padx=values["target_pad_x"], pady=values["target_pad_y"]
        )
        self.target_frame.grid_columnconfigure(2, minsize=values["target_column_gap"])
        for widget in self.target_widgets:
            widget.grid_configure(pady=values["target_row_gap"])
        for widget in self.target_labels:
            widget.grid_configure(padx=(0, values["target_label_gap"]))
        for widget in self.target_combos:
            widget.configure(width=values["target_control_width"])
            widget.grid_configure(padx=(values["target_combo_gap"], 0))
        self.target_load_button.configure(padding=(8, values["target_button_height"]))
        self.feature_panel.configure(
            width=values["feature_width"], height=values["feature_height"]
        )
        self.feature_panel.grid_configure(
            padx=(values["feature_x"], 0), pady=(values["feature_y"], 0)
        )
        self.feature_frame.configure(
            padx=values["feature_pad_x"], pady=values["feature_pad_y"]
        )
        self.hardware_panel.configure(
            width=values["hardware_width"], height=values["hardware_height"]
        )
        self.hardware_panel.grid_configure(
            padx=(values["hardware_x"], 0), pady=(8 + values["hardware_y"], 0)
        )
        self.protocol_panel.configure(
            width=values["protocol_width"], height=values["protocol_height"]
        )
        self.protocol_panel.grid_configure(
            padx=(values["protocol_x"], 0), pady=(values["protocol_y"], 0)
        )
        self.modules_panel.configure(
            width=values["module_width"], height=values["module_height"]
        )
        self.modules_panel.grid_configure(
            padx=(values["module_x"], 0), pady=(values["support_gap"] + values["module_y"], 0)
        )
        self.hardware_frame.configure(
            padx=values["hardware_pad_x"], pady=values["hardware_pad_y"]
        )
        self._regrid_hardware_widgets(
            values["hardware_columns"], values["hardware_label_gap"],
            values["hardware_row_gap"],
        )
        for frame in (self.protocol_frame, self.modules_frame):
            frame.configure(
                padx=values["support_pad_x"], pady=values["support_pad_y"]
            )
            for column in range(values["support_columns"]):
                frame.columnconfigure(column, weight=1)
        self._regrid_support_widgets(
            self.protocol_widgets, values["support_columns"],
            values["support_item_gap"], values["support_row_gap"],
        )
        self._regrid_support_widgets(
            self.module_widgets, values["support_columns"],
            values["support_item_gap"], values["support_row_gap"],
        )
        self.target_panel.event_generate("<Configure>")
        self.feature_panel.event_generate("<Configure>")
        self.hardware_panel.event_generate("<Configure>")
        self.protocol_panel.event_generate("<Configure>")
        self.modules_panel.event_generate("<Configure>")
        self.panels.grid_configure(
            pady=(2, values["panels_bottom_gap"])
        )
        self.button_gap.configure(height=values["button_config_gap"])
        if values["button_config_gap"] == 0:
            self.button_gap.grid_remove()
        else:
            self.button_gap.grid()
        self.buttons_frame.grid_configure(pady=(0, values["button_row_pady"]))
        for button in self.buttons_frame.winfo_children():
            button.pack_configure(padx=values["button_item_padx"])
        self.build_panel.configure(
            width=values["build_width"], height=values["build_height"]
        )
        self.build_panel.grid_configure(
            padx=(values["build_x"], 0), pady=(values["build_y"], 0)
        )
        self.build_frame.configure(
            padx=values["build_pad_x"], pady=values["build_pad_y"]
        )
        self.build_panel.event_generate("<Configure>")
        self.log_frame.grid_configure(
            padx=values["log_pad_x"], pady=(0, values["log_pad_y"])
        )
        self.log.configure(height=values["log_height"])
        if refresh:
            self.root.update_idletasks()

    @staticmethod
    def _regrid_support_widgets(widgets, columns, item_gap, row_gap):
        for index, widget in enumerate(widgets.values()):
            widget.grid_configure(
                row=index // columns + 1,
                column=index % columns,
                padx=(0, item_gap),
                pady=row_gap,
            )

    def _regrid_hardware_widgets(self, columns: int, item_gap: int, row_gap: int) -> None:
        for index, frame in enumerate(self.hardware_option_frames):
            frame.grid_configure(
                row=index // columns + 1,
                column=index % columns,
                sticky="w", padx=(0, item_gap), pady=row_gap,
            )
        for column in range(columns):
            self.hardware_frame.columnconfigure(column, weight=1)
        self.hardware_warning_label.grid_configure(
            row=(len(self.hardware_option_frames) - 1) // columns + 2,
            column=0, columnspan=columns,
        )

    def _layout_payload(self) -> dict[str, int]:
        return {
            name: self._layout_value(name, 1)
            for name in self.layout_vars
        }

    def _layout_text(self) -> str:
        return json.dumps(self._layout_payload(), ensure_ascii=False, indent=2)

    def _copy_layout_params(self):
        text = self._layout_text()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.write_log("布局参数已复制到剪贴板:\n" + text)

    def _save_layout_params(self):
        path = filedialog.asksaveasfilename(
            title="保存布局参数",
            initialfile="layout_debug.json",
            defaultextension=".json",
            filetypes=(("JSON 文件", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        Path(path).write_text(self._layout_text() + "\n", encoding="utf-8")
        self.write_log(f"布局参数已保存: {path}")

    def _reset_layout_params(self):
        defaults = {
            "window_width": 980,
            "window_height": 856,
            "outer_padding": 10,
            "column_gap": 5,
            "left_weight": 1,
            "right_weight": 8,
            "panels_bottom_gap": 10,
            "target_width": 412,
            "target_height": 298,
            "target_x": 1,
            "target_y": 1,
            "target_pad_x": 1,
            "target_pad_y": 1,
            "target_label_gap": 1,
            "target_column_gap": 6,
            "target_row_gap": 6,
            "target_control_width": 8,
            "target_combo_gap": 10,
            "target_button_height": 2,
            "feature_width": 620,
            "feature_height": 102,
            "feature_x": 1,
            "feature_y": 1,
            "feature_pad_x": 1,
            "feature_pad_y": 1,
            "hardware_width": 960,
            "hardware_height": 120,
            "hardware_x": 1,
            "hardware_y": 1,
            "hardware_pad_x": 1,
            "hardware_pad_y": 1,
            "hardware_label_gap": 20,
            "hardware_row_gap": 1,
            "hardware_columns": 3,
            "protocol_width": 620,
            "protocol_height": 92,
            "protocol_x": 1,
            "protocol_y": 2,
            "module_width": 620,
            "module_height": 100,
            "module_x": 1,
            "module_y": 1,
            "support_gap": 1,
            "support_pad_x": 1,
            "support_pad_y": 1,
            "support_columns": 5,
            "support_item_gap": 1,
            "support_row_gap": 1,
            "button_config_gap": 1,
            "button_row_pady": 6,
            "button_item_padx": 3,
            "build_width": 960,
            "build_height": 306,
            "build_x": 1,
            "build_y": 1,
            "build_pad_x": 1,
            "build_pad_y": 1,
            "log_height": 12,
            "log_pad_x": 1,
            "log_pad_y": 1,
        }
        for name, value in defaults.items():
            self.layout_vars[name].set(value)

    def open_layout_debug(self):
        if self.layout_debug_window and self.layout_debug_window.winfo_exists():
            self.layout_debug_window.deiconify()
            self.layout_debug_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.layout_debug_window = window
        window.title("布局调试")
        window.transient(self.root)
        window.resizable(True, True)
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        groups = (
            ("窗口与整体", (
                ("outer_padding", "外边距", 0, 40),
                ("column_gap", "左右列间距", 0, 40),
                ("left_weight", "左列比例", 1, 8),
                ("right_weight", "右列比例", 1, 8),
                ("panels_bottom_gap", "配置区下边距", 0, 40),
            )),
            ("目标配置", (
                ("target_width", "宽度", 180, 800), ("target_height", "高度", 120, 500),
                ("target_x", "位置 X", 0, 200), ("target_y", "位置 Y", 0, 200),
                ("target_pad_x", "横向内边距", 0, 50), ("target_pad_y", "纵向内边距", 0, 50),
                ("target_label_gap", "标签间距", 0, 50), ("target_column_gap", "两列间距", 0, 80),
                ("target_row_gap", "行间距", 0, 30), ("target_control_width", "控件宽度", 8, 30),
                ("target_combo_gap", "下拉框间距", 0, 40), ("target_button_height", "获取按钮高度", 0, 12),
            )),
            ("常用功能", (
                ("feature_width", "宽度", 220, 1000), ("feature_height", "高度", 100, 500),
                ("feature_x", "位置 X", 0, 200), ("feature_y", "位置 Y", 0, 200),
                ("feature_pad_x", "横向内边距", 0, 50), ("feature_pad_y", "纵向内边距", 0, 50),
            )),
            ("硬件配置", (
                ("hardware_width", "宽度", 180, 1200), ("hardware_height", "高度", 120, 500),
                ("hardware_x", "位置 X", 0, 200), ("hardware_y", "位置 Y", 0, 200),
                ("hardware_pad_x", "横向内边距", 0, 50), ("hardware_pad_y", "纵向内边距", 0, 50),
                ("hardware_label_gap", "选项列间距", 0, 60), ("hardware_row_gap", "行间距", 0, 30),
                ("hardware_columns", "选项列数", 1, 4),
            )),
            ("协议与模块", (
                ("protocol_width", "协议宽度", 220, 1000), ("protocol_height", "协议高度", 60, 300),
                ("protocol_x", "协议位置 X", 0, 200), ("protocol_y", "协议位置 Y", 0, 200),
                ("module_width", "模块宽度", 220, 1000), ("module_height", "模块高度", 80, 400),
                ("module_x", "模块位置 X", 0, 200), ("module_y", "模块位置 Y", 0, 200),
                ("support_gap", "两个块间距", 0, 60), ("support_pad_x", "横向内边距", 0, 50),
                ("support_pad_y", "纵向内边距", 0, 50), ("support_columns", "选项列数", 1, 5),
                ("support_item_gap", "选项横向间距", 0, 50), ("support_row_gap", "选项纵向间距", 0, 30),
            )),
            ("构建与日志", (
                ("build_width", "构建块宽度", 400, 1200), ("build_height", "构建块高度", 180, 700),
                ("build_x", "构建块位置 X", 0, 200), ("build_y", "构建块位置 Y", 0, 200),
                ("build_pad_x", "构建块横向内边距", 0, 50), ("build_pad_y", "构建块纵向内边距", 0, 50),
                ("button_config_gap", "按钮上方间距", 0, 60), ("button_row_pady", "按钮下方间距", 0, 40),
                ("button_item_padx", "按钮横向间距", 0, 30), ("log_height", "日志行数", 4, 40),
                ("log_pad_x", "日志横向外边距", 0, 50), ("log_pad_y", "日志下边距", 0, 50),
            )),
        )
        navigation = ttk.Frame(body)
        navigation.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        content = ttk.LabelFrame(body, padding=10)
        content.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        nav_buttons: dict[str, ttk.Button] = {}

        def show_group(title: str, fields) -> None:
            content.configure(text=title)
            for child in content.winfo_children():
                child.destroy()
            for row, (name, label, minimum, maximum) in enumerate(fields):
                ttk.Label(content, text=label, width=18).grid(
                    row=row, column=0, sticky="w", padx=(0, 10), pady=3
                )
                ttk.Spinbox(
                    content, from_=minimum, to=maximum,
                    textvariable=self.layout_vars[name], width=10, justify="right"
                ).grid(row=row, column=1, sticky="w", pady=3)
            for name, button in nav_buttons.items():
                button.configure(style="Primary.TButton" if name == title else "TButton")

        for index, (title, fields) in enumerate(groups):
            button = ttk.Button(
                navigation, text=title,
                command=lambda title=title, fields=fields: show_group(title, fields)
            )
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3)
            navigation.columnconfigure(index % 3, weight=1)
            nav_buttons[title] = button
        show_group(*groups[0])
        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="复制参数", command=self._copy_layout_params).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(buttons, text="保存参数", command=self._save_layout_params).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="恢复默认", command=self._reset_layout_params).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(
            side="right", padx=(6, 0)
        )

    def _build_ui(self):
        outer = ttk.Frame(
            self.root, padding=self.layout_vars["outer_padding"].get(),
            style="App.TFrame",
        )
        self.outer = outer
        outer.pack(side="top", fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(outer, text="EdgeTX 源码目录").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(outer, textvariable=self.source).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(outer, text="浏览", command=self.pick_source).grid(row=row, column=2)
        row += 1
        ttk.Label(outer, text="VS Developer PowerShell 脚本").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(outer, textvariable=self.vs_shell).grid(row=row, column=1, sticky="ew", padx=6)
        ttk.Button(outer, text="浏览", command=self.pick_vs).grid(row=row, column=2)
        row += 1

        ttk.Separator(outer).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        self.panels = panels = ttk.Frame(outer, style="App.TFrame")
        panels.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(2, 6))
        panels.columnconfigure(0, weight=self.layout_vars["left_weight"].get())
        panels.columnconfigure(1, weight=self.layout_vars["right_weight"].get())
        panels.rowconfigure(0, weight=0)
        panels.rowconfigure(1, weight=0)

        left_column = ttk.Frame(panels, style="App.TFrame")
        self.left_column = left_column
        left_column.grid(
            row=0, column=0, sticky="nsew",
            padx=(0, self.layout_vars["column_gap"].get())
        )
        left_column.columnconfigure(0, weight=1)

        target_panel, target_inner = self._make_rounded_panel(
            left_column, "目标配置", self.layout_vars["target_height"].get()
        )
        self.target_panel = target_panel
        self.target_frame = target_inner
        self.target_labels: list[ttk.Label] = []
        self.target_combos: list[ttk.Combobox] = []
        self.target_widgets: list[tk.Widget] = []
        target_panel.grid(row=0, column=0, sticky="w")
        target_inner.columnconfigure((1, 3), weight=1)
        ttk.Label(target_inner, text="目标配置", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 7)
        )
        load_button = ttk.Button(
            target_inner, text="获取发射机型号", command=self.load_targets,
            style="Primary.TButton"
        )
        load_button.grid(row=0, column=3, sticky="e", pady=(0, 3))
        self.target_load_button = load_button
        brand_label = ttk.Label(target_inner, text="厂商", style="Panel.TLabel")
        brand_label.grid(row=1, column=0, sticky="w", pady=3)
        self.brand_combo = ttk.Combobox(
            target_inner, textvariable=self.brand, state="readonly", width=18
        )
        self.brand_combo.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=3)
        self.brand_combo.bind("<<ComboboxSelected>>", lambda _e: self._load_models())
        target_label = ttk.Label(target_inner, text="型号", style="Panel.TLabel")
        target_label.grid(row=2, column=0, sticky="w", pady=3)
        self.target_combo = ttk.Combobox(
            target_inner, textvariable=self.target, state="readonly", width=18
        )
        self.target_combo.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=3)
        self.target_combo.bind("<<ComboboxSelected>>", lambda _e: self._target_dir())
        type_label = ttk.Label(target_inner, text="类型", style="Panel.TLabel")
        type_label.grid(row=3, column=0, sticky="w", pady=3)
        type_combo = ttk.Combobox(
            target_inner, textvariable=self.build_type, values=("Release", "Debug"),
            state="readonly", width=12
        )
        type_combo.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=3)
        mode_label = ttk.Label(target_inner, text="手型设置", style="Panel.TLabel")
        mode_label.grid(row=4, column=0, sticky="w", pady=3)
        mode_combo = ttk.Combobox(
            target_inner, textvariable=self.default_mode,
            values=("默认", "1", "2", "3", "4"), state="readonly", width=10
        )
        mode_combo.grid(row=4, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=3)
        dir_label = ttk.Label(target_inner, text="目录", style="Panel.TLabel")
        dir_label.grid(row=6, column=0, sticky="w", pady=3)
        dir_entry = ttk.Entry(target_inner, textvariable=self.build_dir, width=34)
        dir_entry.grid(row=6, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=3)
        lang_label = ttk.Label(target_inner, text="语言", style="Panel.TLabel")
        lang_label.grid(row=5, column=0, sticky="w", pady=3)
        lang_combo = ttk.Combobox(
            target_inner, textvariable=self.translation, values=LANGUAGES,
            state="readonly", width=12
        )
        lang_combo.grid(row=5, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=3)
        self.target_labels.extend((brand_label, target_label, type_label, mode_label, dir_label, lang_label))
        self.target_combos.extend((self.brand_combo, self.target_combo, type_combo, mode_combo, lang_combo))
        self.target_widgets.extend((brand_label, self.brand_combo, load_button, target_label, self.target_combo, type_label, type_combo, mode_label, mode_combo, dir_label, dir_entry, lang_label, lang_combo))

        right_column = ttk.Frame(panels, style="App.TFrame")
        self.right_column = right_column
        right_column.grid(
            row=0, column=1, sticky="nsew",
            padx=(self.layout_vars["column_gap"].get(), 0)
        )
        right_column.columnconfigure(0, weight=1)

        feature_panel, feature_inner = self._make_rounded_panel(
            right_column, "常用功能", self.layout_vars["feature_height"].get()
        )
        self.feature_panel = feature_panel
        self.feature_frame = feature_inner
        feature_panel.grid(row=0, column=0, sticky="w")
        for column in range(4):
            feature_inner.columnconfigure(column, weight=1)
        ttk.Label(feature_inner, text="常用功能", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4)
        )
        for index, (option, label, _) in enumerate(COMMON_FEATURES):
            ttk.Checkbutton(
                feature_inner, text=label, variable=self.feature_vars[option],
                style="Panel.TCheckbutton"
            ).grid(row=index // 4 + 1, column=index % 4, sticky="w", padx=(0, 6), pady=2)

        protocol_panel, protocols = self._make_rounded_panel(
            right_column, "协议支持", self.layout_vars["protocol_height"].get()
        )
        self.protocol_panel = protocol_panel
        self.protocol_frame = protocols
        protocol_panel.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(protocols, text="协议支持", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 3)
        )
        for column in range(self.layout_vars["support_columns"].get()):
            protocols.columnconfigure(column, weight=1)
        for index, (option, label) in enumerate(PROTOCOL_OPTIONS):
            widget = ttk.Checkbutton(
                protocols, text=label, variable=self.protocol_vars[option]
            )
            widget.grid(
                row=index // self.layout_vars["support_columns"].get() + 1,
                column=index % self.layout_vars["support_columns"].get(),
                sticky="w", padx=(0, self.layout_vars["support_item_gap"].get()),
                pady=self.layout_vars["support_row_gap"].get(),
            )
            self.protocol_widgets[option] = widget
        modules_panel, modules = self._make_rounded_panel(
            right_column, "模块支持", self.layout_vars["module_height"].get()
        )
        self.modules_panel = modules_panel
        self.modules_frame = modules
        modules_panel.grid(
            row=2, column=0, sticky="w",
            pady=(self.layout_vars["support_gap"].get(), 0)
        )
        ttk.Label(modules, text="模块支持", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 3)
        )
        for column in range(self.layout_vars["support_columns"].get()):
            modules.columnconfigure(column, weight=1)
        for index, (option, label) in enumerate(MODULE_OPTIONS):
            widget = ttk.Checkbutton(
                modules, text=label, variable=self.module_vars[option]
            )
            widget.grid(
                row=index // self.layout_vars["support_columns"].get() + 1,
                column=index % self.layout_vars["support_columns"].get(),
                sticky="w", padx=(0, self.layout_vars["support_item_gap"].get()),
                pady=self.layout_vars["support_row_gap"].get(),
            )
            self.module_widgets[option] = widget

        hardware_panel, hardware = self._make_rounded_panel(
            panels, "硬件配置", self.layout_vars["hardware_height"].get()
        )
        self.hardware_panel = hardware_panel
        self.hardware_frame = hardware
        self.hardware_option_frames: list[tk.Frame] = []
        hardware_panel.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(hardware, text="硬件配置", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 5)
        )
        for option, label in HARDWARE_BOOLEAN_OPTIONS:
            frame = tk.Frame(
                hardware, background="#ffffff", bd=0,
                highlightbackground="#c8d8e8", highlightthickness=1,
            )
            frame.columnconfigure(0, minsize=52)
            ttk.Label(frame, text=label, style="Panel.TLabel", width=12).grid(
                row=0, column=0, sticky="w", padx=(5, 3), pady=2
            )
            for column, text in enumerate(HARDWARE_CHOICES, start=1):
                ttk.Radiobutton(
                    frame, text=text, value=text, variable=self.hardware_vars[option],
                    style="Panel.TRadiobutton",
                ).grid(row=0, column=column, sticky="w", padx=(0, 3), pady=2)
            self.hardware_option_frames.append(frame)
        sticks_frame = tk.Frame(
            hardware, background="#ffffff", bd=0,
            highlightbackground="#c8d8e8", highlightthickness=1,
        )
        sticks_frame.columnconfigure(0, minsize=52)
        ttk.Label(sticks_frame, text="摇杆类型", style="Panel.TLabel", width=12).grid(
            row=0, column=0, sticky="w", padx=(5, 3), pady=2
        )
        for column, text in enumerate(("默认", "STANDARD", "HORUS"), start=1):
            ttk.Radiobutton(
                sticks_frame, text=text, value=text, variable=self.sticks_type,
                style="Panel.TRadiobutton",
            ).grid(row=0, column=column, sticky="w", padx=(0, 3), pady=2)
        self.hardware_option_frames.append(sticks_frame)
        self.hardware_warning_label = ttk.Label(
            hardware, textvariable=self.hardware_warning, foreground="#b45309",
            background="#ffffff", justify="left", wraplength=900,
        )
        self._regrid_hardware_widgets(
            self.layout_vars["hardware_columns"].get(),
            self.layout_vars["hardware_label_gap"].get(),
            self.layout_vars["hardware_row_gap"].get(),
        )
        for option, _ in HARDWARE_BOOLEAN_OPTIONS:
            self.hardware_vars[option].trace_add(
                "write", lambda *_args: self._update_hardware_warning()
            )
        self._update_hardware_warning()
        row += 1

        build_panel, build_inner = self._make_rounded_panel(outer, "构建操作", 330)
        self.build_panel = build_panel
        self.build_frame = build_inner
        build_panel.grid(row=row, column=0, columnspan=3, sticky="w")
        build_inner.columnconfigure(0, weight=1)
        ttk.Label(build_inner, text="构建操作", style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        button_gap = ttk.Frame(
            build_inner, height=self.layout_vars["button_config_gap"].get(),
            style="Panel.TFrame"
        )
        button_gap.grid(row=1, column=0, sticky="ew")
        button_gap.grid_propagate(False)
        self.button_gap = button_gap

        buttons = ttk.Frame(build_inner, style="Panel.TFrame")
        self.buttons_frame = buttons
        buttons.grid(
            row=2, column=0, sticky="w",
            pady=(0, self.layout_vars["button_row_pady"].get()),
        )
        for text, command in (
            ("检查环境", self.check_environment),
            ("安装 Python 依赖", self.install_deps),
            ("配置 CMake", self.configure),
            ("编译固件", self.build),
            ("清除日志", self.clear_log),
            ("布局调试", self.open_layout_debug),
        ):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(
                side="left", padx=self.layout_vars["button_item_padx"].get()
            )
        status_row = ttk.Frame(build_inner, style="Panel.TFrame")
        status_row.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.status, style="Panel.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.progress = ttk.Progressbar(status_row, length=320, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="e")
        log_frame = ttk.Frame(build_inner, style="Panel.TFrame")
        self.log_frame = log_frame
        log_frame.grid(
            row=4, column=0, sticky="ew",
            padx=self.layout_vars["log_pad_x"].get(),
            pady=(0, self.layout_vars["log_pad_y"].get()), ipady=3
        )
        self.log = tk.Text(
            log_frame, height=self.layout_vars["log_height"].get(),
            wrap="none", state="disabled",
                           font=("Consolas", 10), undo=False)
        self.log.tag_configure("ok", foreground="#16803c")
        self.log.tag_configure("error", foreground="#c62828")
        self.log.tag_configure("info", foreground="#1565c0")
        self.log.tag_configure("warning", foreground="#ef6c00")
        self.log.tag_configure("normal", foreground="#202124")
        self.log.tag_configure("header", foreground="#37474f", font=("Consolas", 11, "bold"))
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=log_scroll.set)

    def pick_source(self):
        value = filedialog.askdirectory(title="选择 EdgeTX 源码目录")
        if value:
            self.source.set(value)
            self.load_targets()

    def pick_vs(self):
        value = filedialog.askopenfilename(title="选择 Launch-VsDevShell.ps1",
                                           filetypes=(("VS 开发者快捷方式", "*.lnk"),
                                                      ("PowerShell", "*.ps1"),
                                                      ("所有文件", "*.*")))
        if value:
            self.vs_shell.set(value)

    def source_path(self) -> Path | None:
        path = Path(self.source.get().strip())
        if not path.is_dir() or not (path / "CMakeLists.txt").is_file():
            messagebox.showerror("目录无效", "请选择包含 CMakeLists.txt 的 EdgeTX 源码目录。")
            return None
        return path

    def load_targets(self):
        source = self.source_path()
        if not source:
            return
        self.targets = read_target_catalog(source)
        brands = sorted(self.targets, key=str.upper)
        self.brand_combo["values"] = brands
        if self.brand.get() not in self.targets and brands:
            self.brand.set("Jumper" if "Jumper" in self.targets else brands[0])
        self._load_models()
        count = sum(len(models) for models in self.targets.values())
        self.status.set(f"已读取 {len(brands)} 个厂商、{count} 个型号")
        self.write_log(f"厂商: {', '.join(brands)}")

    def _load_models(self):
        models = self.targets.get(self.brand.get(), {})
        names = sorted(models, key=str.upper)
        self.target_combo["values"] = names
        preferred = "Jumper T20"
        if self.target.get() not in models and names:
            self.target.set(preferred if preferred in models else names[0])
        self._target_dir()
        self._load_hardware_defaults()

    def _target_dir(self):
        source = Path(self.source.get().strip())
        if source.is_dir() and self.target.get():
            config = self.targets.get(self.brand.get(), {}).get(self.target.get(), {})
            target = config.get("_target", self.target.get())
            self.build_dir.set(str(source / "build" / target.lower()))

    def _load_hardware_defaults(self):
        config = self.targets.get(self.brand.get(), {}).get(self.target.get(), {})
        target = config.get("_target", self.target.get())
        source = Path(self.source.get().strip())
        self.capabilities = read_target_capabilities(source, target)
        options = read_cmake_options(source, target)
        self.hardware_defaults = read_target_defaults(source, target)
        for option, _ in PROTOCOL_OPTIONS:
            supported = option in self.capabilities
            self.protocol_widgets[option].configure(
                state="normal" if supported else "disabled"
            )
            self.protocol_vars[option].set(
                supported and options.get(option, "OFF") in {"ON", "YES", "TRUE", "1"}
            )
        for option, _ in MODULE_OPTIONS:
            supported = option in self.capabilities
            self.module_widgets[option].configure(
                state="normal" if supported else "disabled"
            )
            self.module_vars[option].set(
                supported and options.get(option, "OFF") in {"ON", "YES", "TRUE", "1"}
            )
        defaults = ", ".join(
            f"{key}={value}" for key, value in self.hardware_defaults.items()
        )
        if defaults:
            self.write_log(f"硬件默认: {target}: {defaults}")
        enabled = [
            option for option, _ in PROTOCOL_OPTIONS + MODULE_OPTIONS
            if option in self.capabilities and
            (self.protocol_vars.get(option, self.module_vars.get(option)).get())
        ]
        self.write_log(f"协议/模块默认: {', '.join(enabled) or '无'}")
        self._update_hardware_warning()

    def _target_cmake_text(self) -> str:
        config = self.targets.get(self.brand.get(), {}).get(self.target.get(), {})
        target = config.get("_target", self.target.get())
        path = Path(self.source.get().strip()) / "radio" / "src" / "targets" / target / "CMakeLists.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _update_hardware_warning(self):
        gimbal = self.hardware_vars["FLYSKY_GIMBAL"].get() == "开启"
        bluetooth = self.hardware_vars["BLUETOOTH"].get() == "开启"
        if not (gimbal and bluetooth):
            self.hardware_warning.set("")
            self.hardware_warning_label.grid_remove()
            return
        text = self._target_cmake_text()
        if "share same UART" in text:
            self.hardware_warning.set(
                "冲突：当前型号的蓝牙模块和 FS 霍尔摇杆共用同一个 UART，"
                "EdgeTX 会自动关闭 FS 霍尔摇杆。请只启用其中一个。"
            )
        else:
            self.hardware_warning.set(
                "提示：FS 霍尔摇杆与蓝牙同时启用，请确认当前硬件有独立串口；"
                "部分型号会由目标 CMake 自动关闭其中一项。"
            )
        self.hardware_warning_label.grid()

    def write_log(self, text: str):
        self.log.configure(state="normal")
        for line in text.splitlines() or [""]:
            upper = line.upper()
            if line.startswith("***") or line.startswith("** "):
                tag = "header"
            elif "OK" in upper or "SUCCESS" in upper or "完成" in line:
                tag = "ok"
            elif "缺失" in line or "未完成" in line or "FAILED" in upper or "ERROR" in upper or "失败" in line:
                tag = "error"
            elif "WARNING" in upper or "警告" in line or "NO WORK" in upper:
                tag = "warning"
            elif line.startswith(("目标板:", "正在", "检查", "配置", "编译")):
                tag = "info"
            else:
                tag = "normal"
            self.log.insert("end", line + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status.set("日志已清除")

    def run_async(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def spinner_tick(self):
        if self.busy:
            frames = ("|", "/", "-", "\\")
            frame = frames[self.spinner_index % len(frames)]
            self.spinner_index += 1
            self.status.set(f"{self.status.get().split('  ')[0]}  {frame}")
        self.root.after(180, self.spinner_tick)

    def check_environment(self):
        source = self.source_path()
        if not source:
            return
        self.load_targets()
        self.run_async(lambda: self._check_worker(source))

    def _check_worker(self, source: Path):
        self.events.put(("status", "正在检查工具、子模块和 Python 包..."))
        lines = []
        for tool in TOOLS:
            found = shutil.which(tool)
            lines.append(f"{'OK' if found else '缺失':<4} {tool}: {found or '-'}")
            if not found:
                lines.append(f"  处理方法: {MISSING_TOOL_HELP[tool]}")
        code, output = run_capture(["git", "submodule", "status", "--recursive"], source)
        sub_ok = code == 0 and output and all(not line.startswith(("-", "+", "U"))
                                              for line in output.splitlines())
        lines.append(f"{'OK' if sub_ok else '未完成'} 子模块初始化")
        if not sub_ok:
            lines.append("  处理方法: 在源码目录执行 git submodule update --init --recursive")
        if output:
            for line in output.splitlines():
                match = re.match(r"^([0-9a-f]{7,40})\s+(.*)$", line.strip())
                lines.append(f"[{match.group(1)}] {match.group(2)}" if match else line.strip())
        for module, package in REQUIRED_IMPORTS.items():
            check = f"import {module}" if "." not in module else f"import {module}"
            code, _ = run_capture([sys.executable, "-c", check])
            lines.append(f"{'OK' if code == 0 else '缺失'} Python: {package}")
            if code != 0:
                lines.append(f"  处理方法: 执行 {sys.executable} -m pip install {package}")
        path_clang, path_clang_version = path_libclang()
        preferred_clang, preferred_clang_version = find_compatible_libclang()
        if preferred_clang:
            lines.append(
                f"OK   libclang: VS LLVM {preferred_clang_version} ({preferred_clang})"
            )
            if path_clang and path_clang != preferred_clang:
                lines.append(
                    f"  当前 PATH 先命中 LLVM {path_clang_version or '?'} ({path_clang})；"
                    "构建器会自动优先使用 VS LLVM。"
                )
        elif path_clang_version is not None and path_clang_version >= 20:
            lines.append(f"OK   libclang: LLVM {path_clang_version} ({path_clang})")
        else:
            lines.append(
                f"警告 libclang: 当前版本 {path_clang_version or '未检测到'} "
                f"({path_clang or '-'})"
            )
            lines.append(
                "  处理方法: 安装 LLVM/Clang 20 或更高版本，并将其 bin 目录添加到 PATH 后重启本程序。"
            )
        self.events.put(("log", "\n".join(lines)))
        self.events.put(("status", "检查完成；缺失项请按需点击安装或修复"))

    def install_deps(self):
        source = self.source_path()
        if not source:
            return
        requirements = source / "requirements.txt"
        if not requirements.is_file():
            messagebox.showerror("缺少文件", "源码目录中没有 requirements.txt。")
            return
        if not messagebox.askyesno("确认安装", "将使用当前 Python 执行 pip install -r requirements.txt，是否继续？"):
            return
        self.run_async(lambda: self._command_worker(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)], source,
            "Python 依赖安装完成"))

    def _ps_prefix(self) -> str:
        script = self.vs_shell.get().strip()
        if not script:
            return ""
        escaped = script.replace("'", "''")
        if script.lower().endswith(".lnk"):
            # Developer PowerShell shortcuts normally launch powershell.exe
            # with a -Command argument that loads Launch-VsDevShell.ps1.
            # Resolve and replay that command in this same child shell so its
            # environment is inherited by CMake/Ninja.
            shortcut = escaped
            prefix = (
                "$w=New-Object -ComObject WScript.Shell; "
                f"$s=$w.CreateShortcut('{shortcut}'); "
                "$t=$s.TargetPath; "
                "$a=$s.Arguments; "
                "if($t -match '(?i)\\.ps1$'){ "
                "  & $t -Arch amd64 -HostArch amd64; "
                "} else { "
                "$m=[regex]::Match($a,'(?is)-Command\\s+(.+)$'); "
                "if(-not $m.Success){$m=[regex]::Match($a,'(?is)(?:^|\\s)-c\\s+(.+)$')}; "
                "if(-not $m.Success){throw ('无法从快捷方式读取 Developer PowerShell 命令。Target=' + $t + ' Arguments=' + $a)}; "
                "$vs=$m.Groups[1].Value.Trim(); "
                # Some Windows .lnk files expose escaped arguments with
                # doubled/tripled quotes around paths containing spaces.
                "$vs=$vs.Replace('\"\"\"','\"').Replace('\"\"','\"'); "
                "$vs=$vs -replace '^\\\"|\\\"$',''; "
                "$vs=$vs -replace '(?i)-NoExit\\s*',''; "
                "Invoke-Expression $vs; "
                "}; "
            )
        else:
            prefix = f"& '{escaped}' -Arch amd64 -HostArch amd64; "

        libclang_dir, _ = find_compatible_libclang()
        if libclang_dir:
            clang_path = str(libclang_dir).replace("'", "''")
            prefix += f"$env:PATH='{clang_path};'+$env:PATH; "
        return prefix

    def cmake_args(self) -> tuple[Path, list[str]] | None:
        source = self.source_path()
        if not source:
            return None
        if not self.targets:
            self.targets = read_target_catalog(source)
        config = self.targets.get(self.brand.get(), {}).get(self.target.get())
        if not config:
            messagebox.showerror("目标无效", "请先读取目标板，然后选择厂商和型号。")
            return None
        target = config["_target"]
        build = Path(self.build_dir.get().strip() or source / "build" / target.lower())
        args = ["cmake", "--fresh", "-S", str(source), "-B", str(build), "-G", "Ninja"]
        options = {key: value for key, value in config.items() if not key.startswith("_")}
        for option, _, _ in COMMON_FEATURES:
            options[option] = "YES" if self.feature_vars[option].get() else "NO"
        if self.default_mode.get() != "默认":
            options["DEFAULT_MODE"] = self.default_mode.get()
        for option, _ in PROTOCOL_OPTIONS:
            if option in self.capabilities:
                options[option] = "YES" if self.protocol_vars[option].get() else "NO"
        for option, _ in MODULE_OPTIONS:
            if option in self.capabilities:
                options[option] = "YES" if self.module_vars[option].get() else "NO"
        for option, _ in HARDWARE_BOOLEAN_OPTIONS:
            value = HARDWARE_CHOICES[self.hardware_vars[option].get()]
            if value is not None:
                options[option] = value
        if self.sticks_type.get() != "默认":
            options["STICKS"] = self.sticks_type.get()
        args += [f"-D{key}={value}" for key, value in options.items()]
        args += [f"-DTRANSLATIONS={self.translation.get().strip() or 'EN'}",
                 f"-DCMAKE_BUILD_TYPE={self.build_type.get()}",
                 "-DCMAKE_C_COMPILER=cl.exe", "-DCMAKE_CXX_COMPILER=cl.exe"]
        return build, args

    def configure(self):
        gimbal = self.hardware_vars["FLYSKY_GIMBAL"].get() == "开启"
        bluetooth = self.hardware_vars["BLUETOOTH"].get() == "开启"
        if gimbal and bluetooth and "share same UART" in self._target_cmake_text():
            messagebox.showwarning(
                "硬件配置冲突",
                "当前型号的蓝牙模块和 FS 霍尔摇杆共用同一个 UART，"
                "不能同时启用。请返回硬件配置后只选择其中一个。"
            )
            return
        result = self.cmake_args()
        if result:
            build, args = result
            self.run_async(lambda: self._command_worker(args, Path(self.source.get()),
                                                        "CMake 配置完成", shell=True))

    def build(self):
        result = self.cmake_args()
        if result:
            build, _ = result
            cmd = ["cmake", "--build", str(build), "--target", "arm-none-eabi-configure"]
            self.run_async(lambda: self._build_worker(cmd, build, "arm-none-eabi-configure"))

    def _build_worker(self, configure_cmd: list[str], build: Path, label: str):
        self.events.put(("status", "正在配置 ARM 固件子工程，请稍候..."))
        ok = self._command_worker(configure_cmd, Path(self.source.get()), label, shell=True)
        if ok:
            self.events.put(("status", "ARM 子工程配置完成，正在编译固件..."))
            self._command_worker(["cmake", "--build", str(build / "arm-none-eabi"),
                                  "--target", "firmware", "--parallel"],
                                 Path(self.source.get()), "固件编译完成", shell=True)

    def _command_worker(self, args: list[str], cwd: Path, success: str, shell: bool = False):
        # PowerShell needs the call operator for a quoted executable. Without
        # '&', a command such as "cmake" "--build" is parsed as two strings.
        def ps_quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        command = "& " + ps_quote(args[0])
        if len(args) > 1:
            command += " " + " ".join(ps_quote(a) for a in args[1:])
        prefix = self._ps_prefix()
        if prefix:
            command = prefix + command
        self.events.put(("status", f"正在执行: {args[0]} ..."))
        self.events.put(("busy", "1"))
        def stream_line(line: str):
            self.events.put(("log", line))
            match = re.search(r"\[(\d+)\s*/\s*(\d+)\]", line)
            if match:
                self.events.put(("progress", f"{match.group(1)}/{match.group(2)}"))

        code, output = run_capture(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                    "-Command", command], cwd, on_line=stream_line)
        if not output:
            self.events.put(("log", command))
        if code == 0 and "no work to do" in output.lower() and success == "固件编译完成":
            result = "当前目标已经是最新状态，无需重新编译"
        else:
            result = success if code == 0 else f"失败，退出码 {code}"
        self.events.put(("status", result))
        if code == 0 and success == "固件编译完成":
            self.events.put(("log", "编译成功了，固件已生成。" if "no work to do" not in output.lower()
                             else "当前目标已经是最新状态，无需重新编译。"))
        self.events.put(("busy", "0"))
        return code == 0

    def _drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "busy":
                    self.busy = value == "1"
                elif kind == "progress":
                    done, total = (int(x) for x in value.split("/"))
                    self.progress.configure(maximum=total, value=done)
                elif kind == "log":
                    self.write_log(value)
                else:
                    self.status.set(value)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)


if __name__ == "__main__":
    app_root = tk.Tk()
    App(app_root)
    app_root.mainloop()
