"""EdgeTX Builder for Windows.

Single-file application for configuring and building EdgeTX targets.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import app_settings
import elrs_backend
from builder_utils import external_python, resource_path, run_capture
from edgetx_backend import (
    MISSING_TOOL_HELP,
    PYTHON_PACKAGE_ALIASES,
    REQUIRED_IMPORTS,
    TOOLS,
    find_compatible_libclang,
    path_libclang,
    read_cmake_options,
    read_target_catalog,
    read_target_capabilities,
    read_target_defaults,
)


# GUI application.

DEFAULT_SOURCE = r"E:\Code\edgetx"
DEFAULT_WINDOW_WIDTH = 980
DEFAULT_WINDOW_HEIGHT = 900
ELRS_MANUAL_MODE = "（手动选择目标）"
DEFAULT_VS_SHELL = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Visual Studio\Visual Studio Tools\Developer PowerShell for VS.lnk"
APP_NAME = "EdgeTX Builder"
APP_VERSION = "1.0"
APP_AUTHOR = "大魔王~"
APP_EMAIL = "13471217561@163.com"
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
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("EdgeTX Builder - Windows")
        self._set_window_icon()
        self.root.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}")
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
        self.system = tk.StringVar(value="edgetx")
        self.elrs_source = tk.StringVar(value=elrs_backend.DEFAULT_SOURCE)
        self.elrs_kind = tk.StringVar(value="TX")
        self.elrs_env = tk.StringVar(value="")
        self.elrs_envs: dict[str, list[str]] = {}
        self.elrs_domain = tk.StringVar(value="默认")
        self.elrs_binding = tk.StringVar(value="")
        self.elrs_lock = tk.BooleanVar(value=False)
        self.elrs_defines_loaded = False
        self.elrs_vendor = tk.StringVar(value=ELRS_MANUAL_MODE)
        self.elrs_product = tk.StringVar(value="")
        self.elrs_layout = tk.StringVar(value="")
        self.elrs_artifact = tk.StringVar(value="")
        self.elrs_hardware: dict[str, dict[str, dict]] = {}
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
            "window_width": tk.IntVar(value=DEFAULT_WINDOW_WIDTH),
            "window_height": tk.IntVar(value=DEFAULT_WINDOW_HEIGHT),
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
        self.settings_window: tk.Toplevel | None = None
        self.layout_viewer_window: tk.Toplevel | None = None

        self._setup_theme()
        self._build_ui()
        self._setup_context_menu()
        for variable in self.layout_vars.values():
            variable.trace_add("write", self._layout_changed)
        self._apply_layout()
        self.root.after(150, self._drain_events)
        self.root.after(180, self.spinner_tick)

    def _set_window_icon(self):
        icon = resource_path("edgetx_tx.ico")
        if icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _setup_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=False)
        self.context_menu.add_command(label="关于", command=self.show_about)
        self._bind_context_menu(self.root)
        self._bind_context_menu(self.outer)

    def _bind_context_menu(self, widget):
        if isinstance(widget, (tk.Frame, ttk.Frame, tk.Canvas)):
            widget.bind("<Button-3>", self._show_context_menu, add="+")
        for child in widget.winfo_children():
            self._bind_context_menu(child)

    def _show_context_menu(self, event):
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def show_about(self):
        messagebox.showinfo(
            "关于",
            f"{APP_NAME}\n"
            f"版本：{APP_VERSION}\n\n"
            f"作者：{APP_AUTHOR}\n"
            f"邮箱：{APP_EMAIL}\n\n"
            "用于 Windows 下配置和构建 EdgeTX 固件。",
            parent=self.root,
        )

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
        width = self._layout_value("window_width", DEFAULT_WINDOW_WIDTH)
        height = self._layout_value("window_height", DEFAULT_WINDOW_HEIGHT)
        self.root.geometry(f"{width}x{height}")
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
        if hasattr(self, "elrs_panel"):
            self._apply_system_visibility()
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
            "window_width": DEFAULT_WINDOW_WIDTH,
            "window_height": DEFAULT_WINDOW_HEIGHT,
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

        system_row = ttk.Frame(outer, style="App.TFrame")
        system_row.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(system_row, text="构建系统").pack(side="left", padx=(0, 8))
        self.system_buttons: dict[str, ttk.Button] = {}
        for name, text in (("edgetx", "EdgeTX 固件"), ("elrs", "ExpressLRS")):
            button = ttk.Button(
                system_row, text=text, width=14,
                command=lambda n=name: self.switch_system(n),
            )
            button.pack(side="left", padx=3)
            self.system_buttons[name] = button
        ttk.Button(
            system_row, text="路径设置", command=self.open_settings,
        ).pack(side="right", padx=(10, 0))
        row += 1
        self.source_label = ttk.Label(outer, text="EdgeTX 源码目录")
        self.source_label.grid(row=row, column=0, sticky="w", pady=4)
        self.source_entry = ttk.Entry(outer, textvariable=self.source)
        self.source_entry.grid(row=row, column=1, sticky="ew", padx=6)
        self.source_button = ttk.Button(outer, text="浏览", command=self.pick_source)
        self.source_button.grid(row=row, column=2)
        row += 1
        self.vs_label = ttk.Label(outer, text="VS Developer PowerShell 脚本")
        self.vs_label.grid(row=row, column=0, sticky="w", pady=4)
        self.vs_entry = ttk.Entry(outer, textvariable=self.vs_shell)
        self.vs_entry.grid(row=row, column=1, sticky="ew", padx=6)
        self.vs_button = ttk.Button(outer, text="浏览", command=self.pick_vs)
        self.vs_button.grid(row=row, column=2)
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

        self._build_elrs_panel(panels)

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
        self.action_buttons: dict[str, ttk.Button] = {}
        for key, text, command in (
            ("check", "检查环境", self.check_environment),
            ("deps", "安装 Python 依赖", self.install_deps),
            ("configure", "配置 CMake", self.configure),
            ("build", "编译固件", self.build),
            ("clear", "清除日志", self.clear_log),
        ):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(
                side="left", padx=self.layout_vars["button_item_padx"].get()
            )
            self.action_buttons[key] = button
        self.switch_system(self.system.get())
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

    def _build_elrs_panel(self, panels):
        panel, inner = self._make_rounded_panel(panels, "ExpressLRS 配置", 430)
        panel.configure(width=960)
        self.elrs_panel = panel
        self.elrs_frame = inner
        inner.columnconfigure(1, weight=1)
        ttk.Label(inner, text="ExpressLRS 配置", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )
        ttk.Button(
            inner, text="读取编译目标 / 型号", command=self.load_elrs_targets,
            style="Primary.TButton",
        ).grid(row=0, column=3, sticky="e", pady=(0, 12))
        ttk.Label(inner, text="设备类型", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=6
        )
        kind_combo = ttk.Combobox(
            inner, textvariable=self.elrs_kind, values=("TX", "RX"),
            state="readonly", width=10,
        )
        kind_combo.grid(row=1, column=1, sticky="w", padx=(10, 20), pady=6)
        kind_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_elrs_kind_changed())
        ttk.Label(inner, text="厂商", style="Panel.TLabel").grid(
            row=1, column=2, sticky="w", pady=6
        )
        self.elrs_vendor_combo = ttk.Combobox(
            inner, textvariable=self.elrs_vendor, values=(ELRS_MANUAL_MODE,),
            state="readonly", width=26,
        )
        self.elrs_vendor_combo.grid(row=1, column=3, sticky="w", padx=(10, 0), pady=6)
        self.elrs_vendor_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._refresh_elrs_products()
        )
        ttk.Label(inner, text="编译目标", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", pady=6
        )
        self.elrs_env_combo = ttk.Combobox(
            inner, textvariable=self.elrs_env, state="readonly", width=44
        )
        self.elrs_env_combo.grid(row=2, column=1, sticky="w", padx=(10, 20), pady=6)
        ttk.Label(inner, text="型号", style="Panel.TLabel").grid(
            row=2, column=2, sticky="w", pady=6
        )
        self.elrs_product_combo = ttk.Combobox(
            inner, textvariable=self.elrs_product, state="readonly", width=26
        )
        self.elrs_product_combo.grid(row=2, column=3, sticky="w", padx=(10, 0), pady=6)
        self.elrs_product_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_elrs_product_selected()
        )
        ttk.Label(
            inner, textvariable=self.elrs_layout, style="Panel.TLabel",
            foreground="#1565c0", wraplength=880,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(12, 6))
        ttk.Label(
            inner,
            text="提示：PlatformIO 装在独立目录 tools\\pio-venv，不需要 VSCode；首次编译需联网下载工具链，请耐心等待。",
            style="Panel.TLabel", foreground="#b45309", wraplength=880,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 4))
        ttk.Label(inner, text="user_defines 常用配置", style="PanelTitle.TLabel").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(14, 6)
        )
        ttk.Label(inner, text="频段", style="Panel.TLabel").grid(
            row=6, column=0, sticky="w", pady=6
        )
        ttk.Combobox(
            inner, textvariable=self.elrs_domain,
            values=("默认", *elrs_backend.REGULATORY_DOMAINS),
            state="readonly", width=28,
        ).grid(row=6, column=1, sticky="w", padx=(10, 20), pady=6)
        ttk.Label(inner, text="绑定短语", style="Panel.TLabel").grid(
            row=6, column=2, sticky="w", pady=6
        )
        ttk.Entry(inner, textvariable=self.elrs_binding, width=32).grid(
            row=6, column=3, sticky="w", padx=(10, 0), pady=6
        )
        ttk.Checkbutton(
            inner, text="首次连接锁定", variable=self.elrs_lock,
            style="Panel.TCheckbutton",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(
            inner, text="保存 user_defines", command=self.save_user_defines,
        ).grid(row=7, column=2, sticky="w", pady=6)
        ttk.Button(
            inner, text="打开 user_defines.txt", command=self.open_user_defines,
        ).grid(row=7, column=3, sticky="w", pady=6)
        ttk.Button(
            inner, text="打开产物目录", command=self.open_elrs_artifact_dir,
            style="Primary.TButton",
        ).grid(row=8, column=0, sticky="w", pady=(10, 6))
        ttk.Label(
            inner, textvariable=self.elrs_artifact, style="Panel.TLabel",
            foreground="#5f6368", wraplength=520,
        ).grid(row=8, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(10, 6))
        ttk.Button(
            inner, text="查看硬件配置", command=self.open_layout_viewer,
        ).grid(row=8, column=3, sticky="e", pady=(10, 6))

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title("数据目录设置")
        window.transient(self.root)
        window.geometry("680x400")
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="打包成 exe 后程序目录可能只读，以下目录均可由用户选择；改动后 PlatformIO 可能需要重新安装或重新下载工具链。",
            wraplength=640, justify="left",
        ).pack(anchor="w", pady=(0, 10))
        summary = tk.StringVar()

        def refresh():
            values = app_settings.paths()
            core = values["pio_core"] or "PlatformIO 默认位置（用户目录 .platformio）"
            summary.set(
                f"数据工作目录: {values['workspace']}\n"
                f"PlatformIO 虚拟环境: {values['pio_venv']}\n"
                f"PlatformIO 工具链缓存: {core}\n"
                f"配置副本目录: {values['overrides']}\n"
                f"固件副本目录: {values['output']}"
            )

        ttk.Label(body, textvariable=summary, justify="left", wraplength=640).pack(
            anchor="w", fill="x"
        )
        core_enabled = tk.BooleanVar(value=app_settings.pio_core_enabled())

        def toggle_core():
            app_settings.set_pio_core_enabled(core_enabled.get())
            refresh()

        ttk.Checkbutton(
            body, text="使用自定义 PlatformIO 工具链缓存目录（会重新下载）",
            variable=core_enabled, command=toggle_core,
        ).pack(anchor="w", pady=(12, 4))
        buttons = ttk.Frame(body)
        buttons.pack(anchor="w", pady=(12, 0))

        def choose():
            value = filedialog.askdirectory(title="选择数据工作目录")
            if value:
                app_settings.set_workspace(Path(value))
                refresh()
                self.write_log(f"数据工作目录已设置: {value}")

        def reset():
            app_settings.reset_workspace()
            app_settings.set_pio_core_enabled(False)
            core_enabled.set(False)
            refresh()
            self.write_log("数据目录已恢复默认")

        ttk.Button(buttons, text="选择工作目录", command=choose).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="恢复默认", command=reset).pack(side="left", padx=6)
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="left", padx=6)
        refresh()

    def open_layout_viewer(self):
        if self.layout_viewer_window and self.layout_viewer_window.winfo_exists():
            self.layout_viewer_window.destroy()
        item = self.elrs_hardware.get(self.elrs_vendor.get(), {}).get(self.elrs_product.get())
        if not item:
            messagebox.showinfo("请先选择型号", "请先在厂商和型号下拉中选择一个具体型号。")
            return
        source = Path(self.elrs_source.get().strip())
        info = elrs_backend.read_layout_config(source, item["config"])
        if not info["entries"]:
            messagebox.showinfo("无配置", info["warning"] or "该型号没有可显示的 layout 配置。")
            return
        self.layout_info = info
        self.layout_config = item["config"]
        self.layout_values = {entry["key"]: entry["value"] for entry in info["entries"]}
        self.layout_original = dict(self.layout_values)
        self.layout_edits: set[str] = set()
        active = app_settings.active_layout()
        active_hint = ""
        if active and active.get("config") == item["config"]:
            active_hint = " 已启用活动配置，编译后会自动应用。"
        self.layout_status = tk.StringVar(
            value="双击表格行编辑数值；保存时会生成配置副本与固件副本，不改动原 layout 文件和原固件。"
                  + active_hint
        )
        window = tk.Toplevel(self.root)
        self.layout_viewer_window = window
        window.title(f"硬件配置 - {info['product_name']}")
        window.geometry("760x590")
        body = ttk.Frame(window, padding=10)
        body.pack(fill="both", expand=True)
        overlay_count = sum(1 for entry in info["entries"] if "overlay" in entry["source"])
        ttk.Label(body, text=f"型号: {info['product_name']}").pack(anchor="w")
        ttk.Label(body, text=f"layout 文件: {info['layout_file']}").pack(anchor="w")
        ttk.Label(
            body,
            text=f"条目: {len(info['entries'])}（其中 overlay 覆盖 {overlay_count} 项）；显示最终生效值",
        ).pack(anchor="w", pady=(0, 6))
        device_row = ttk.Frame(body)
        device_row.pack(fill="x", pady=(4, 6))
        ttk.Label(device_row, text="Lua 设备名").pack(side="left")
        self.layout_device_name = tk.StringVar(value=info["lua_name"])
        ttk.Entry(
            device_row, textvariable=self.layout_device_name, width=24,
        ).pack(side="left", padx=8)
        ttk.Label(
            device_row, text="最多 16 字节，写入固件尾部后由 Lua 读取",
            foreground="#5f6368",
        ).pack(side="left")
        ttk.Label(body, text="搜索字段").pack(anchor="w")
        search_var = tk.StringVar()
        ttk.Entry(body, textvariable=search_var).pack(fill="x", pady=(2, 6))
        frame = ttk.Frame(body)
        frame.pack(fill="both", expand=True)
        columns = ("key", "type", "value", "source")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        for column, text, width in (
            ("key", "字段", 220), ("type", "类型", 80),
            ("value", "当前值", 280), ("source", "来源 / 状态", 140),
        ):
            tree.heading(column, text=text)
            tree.column(column, width=width, anchor="w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tree.bind("<Double-1>", lambda _event: self._edit_layout_value())
        self.layout_tree = tree

        def display(value):
            if isinstance(value, list):
                return ", ".join(str(item) for item in value)
            if value is True:
                return "true"
            if value is False:
                return "false"
            return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, str)) else str(value)

        def refresh(*_args):
            for row in tree.get_children():
                tree.delete(row)
            needle = search_var.get().strip().lower()
            for entry in info["entries"]:
                if needle and needle not in entry["key"].lower():
                    continue
                key = entry["key"]
                status = "已编辑" if key in self.layout_edits else entry["source"]
                tree.insert("", "end", values=(
                    key, type(self.layout_values[key]).__name__,
                    display(self.layout_values[key]), status,
                ))

        self.layout_display = display
        self.layout_refresh = refresh
        search_var.trace_add("write", refresh)
        refresh()
        ttk.Label(
            body, textvariable=self.layout_status, foreground="#b45309",
            wraplength=720, justify="left",
        ).pack(anchor="w", fill="x", pady=(8, 4))
        actions = ttk.Frame(body)
        actions.pack(anchor="e")
        ttk.Button(
            actions, text="保存配置副本", command=self.save_layout_override,
            style="Primary.TButton",
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="应用配置", command=self.apply_layout_to_firmware,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="清除活动配置", command=self.clear_layout_override,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="关闭", command=window.destroy).pack(side="left")
        self.layout_search = search_var

    def _edit_layout_value(self):
        selection = self.layout_tree.selection()
        if not selection:
            return
        key = str(self.layout_tree.item(selection[0], "values")[0])
        original = self.layout_original[key]
        editor = tk.Toplevel(self.layout_viewer_window)
        editor.title(f"编辑 - {key}")
        editor.transient(self.layout_viewer_window)
        editor.geometry("440x180")
        frame = ttk.Frame(editor, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"字段: {key}    类型: {type(original).__name__}").pack(anchor="w")
        value = tk.StringVar(value=self.layout_display(self.layout_values[key]))
        entry = ttk.Entry(frame, textvariable=value, width=48)
        entry.pack(fill="x", pady=8)
        entry.focus_set()
        ttk.Label(frame, text="数组用逗号分隔；布尔值用 true / false", foreground="#5f6368").pack(anchor="w")

        def apply():
            try:
                parsed = elrs_backend.parse_layout_input(value.get(), original)
            except (ValueError, TypeError) as exc:
                messagebox.showerror("输入无效", str(exc), parent=editor)
                return
            self.layout_values[key] = parsed
            self.layout_edits.add(key)
            ok, msg = elrs_backend.validate_layout(self.layout_values)
            self.layout_status.set(
                f"{key} 已修改，尚未保存副本。" if ok else f"配置无效: {msg}"
            )
            self.layout_refresh()
            editor.destroy()

        ttk.Button(frame, text="确定", command=apply).pack(side="right", padx=(6, 0))
        ttk.Button(frame, text="取消", command=editor.destroy).pack(side="right")

    def save_layout_override(self):
        if not getattr(self, "layout_values", None):
            return
        ok, message = elrs_backend.validate_layout(self.layout_values)
        if not ok:
            messagebox.showerror("配置无效", message)
            return
        source = Path(self.elrs_source.get().strip())
        device_name = self.layout_device_name.get().strip() or None
        result = elrs_backend.write_layout_override(
            source, self.elrs_env.get(),
            self.layout_info["product_name"], self.layout_values, device_name,
        )
        if not result["json"]:
            messagebox.showerror("保存失败", result["warning"])
            return
        app_settings.set_active_layout({
            "config": self.layout_config,
            "product": self.layout_info["product_name"],
            "json": result["json"],
            "env": self.elrs_env.get(),
            "device_name": device_name or "",
        })
        lines = [f"配置副本: {result['json']}（{result['bytes']} 字节）"]
        if device_name:
            lines.append(f"Lua 设备名: {device_name}")
        if result["bin"]:
            lines.append(f"固件副本: {result['bin']}")
        lines.append("已设为活动配置：之后每次编译会自动应用到固件副本。")
        if result["warning"]:
            lines.append(result["warning"])
        text = "\n".join(lines)
        self.layout_status.set(text)
        self.write_log("硬件配置副本已生成：\n" + text)
        if result["bin"]:
            self.elrs_artifact.set(result["bin"])

    def apply_layout_to_firmware(self):
        active = app_settings.active_layout()
        if not active:
            messagebox.showinfo("无活动配置", "请先编辑并保存配置副本。")
            return
        source = Path(self.elrs_source.get().strip())
        env = self.elrs_env.get() or str(active.get("env", ""))
        result = elrs_backend.apply_active_layout(
            source, env, str(active.get("config", ""))
        )
        if not result or not result.get("bin"):
            messagebox.showerror(
                "应用失败",
                (result or {}).get("warning", "未找到活动配置或已编译的 firmware.bin。"),
            )
            return
        text = f"已应用自定义配置: {result['bin']}"
        self.layout_status.set(text)
        self.write_log(text)
        self.elrs_artifact.set(result["bin"])

    def clear_layout_override(self):
        app_settings.clear_active_layout()
        text = "已清除活动配置：后续编译不再自动应用自定义 layout。"
        self.layout_status.set(text)
        self.write_log(text)

    def switch_system(self, name: str):
        is_elrs = name == "elrs"
        self.system.set(name)
        for key, button in self.system_buttons.items():
            button.configure(style="Primary.TButton" if key == name else "TButton")
        self.root.title("ExpressLRS Builder - Windows" if is_elrs else "EdgeTX Builder - Windows")
        self.source_label.configure(text="ELRS 源码目录" if is_elrs else "EdgeTX 源码目录")
        self.source_entry.configure(textvariable=self.elrs_source if is_elrs else self.source)
        for widget in (self.vs_label, self.vs_entry, self.vs_button):
            widget.configure(state="disabled" if is_elrs else "normal")
        self.action_buttons["deps"].configure(
            text="安装 PlatformIO" if is_elrs else "安装 Python 依赖"
        )
        configure_button = self.action_buttons["configure"]
        if is_elrs:
            configure_button.pack_forget()
        else:
            configure_button.pack(
                side="left", before=self.action_buttons["build"],
                padx=self.layout_vars["button_item_padx"].get(),
            )
        self._apply_system_visibility()
        if is_elrs and not self.elrs_defines_loaded:
            self.load_user_defines()
        self.status.set(
            "请选择 ELRS 源码目录，然后点击检查环境" if is_elrs
            else "请选择 EdgeTX 源码目录，然后点击检查环境"
        )

    def _apply_system_visibility(self):
        is_elrs = self.system.get() == "elrs"
        for widget in (self.left_column, self.right_column, self.hardware_panel):
            if is_elrs:
                widget.grid_remove()
            else:
                widget.grid()
        if is_elrs:
            self.elrs_panel.grid(row=0, column=0, columnspan=2, sticky="w")
        else:
            self.elrs_panel.grid_remove()

    def _load_hardware_catalog(self):
        source = Path(self.elrs_source.get().strip())
        self.elrs_hardware = elrs_backend.read_hardware_catalog(source, self.elrs_envs)
        vendors = [ELRS_MANUAL_MODE, *sorted(self.elrs_hardware, key=str.upper)]
        self.elrs_vendor_combo["values"] = vendors
        if self.elrs_vendor.get() not in self.elrs_hardware:
            self.elrs_vendor.set(ELRS_MANUAL_MODE)
        products = [item for items in self.elrs_hardware.values() for item in items.values()]
        buildable = sum(1 for item in products if item["envs"])
        self.write_log(
            f"ELRS 硬件目录: {len(self.elrs_hardware)} 厂商 / {len(products)} 型号，"
            f"当前源码可编译 {buildable} 个"
        )
        self._refresh_elrs_products()

    def _on_elrs_kind_changed(self):
        if self.elrs_vendor.get() == ELRS_MANUAL_MODE:
            self._refresh_elrs_envs()
        else:
            self._refresh_elrs_products()

    def _refresh_elrs_products(self):
        vendor = self.elrs_vendor.get()
        if vendor == ELRS_MANUAL_MODE or vendor not in self.elrs_hardware:
            self.elrs_product_combo["values"] = ()
            self.elrs_product.set("")
            self.elrs_layout.set("手动模式：可直接按 TX/RX 选择编译目标。")
            self._refresh_elrs_envs()
            return
        items = {
            label: item for label, item in self.elrs_hardware[vendor].items()
            if item["kind"] == self.elrs_kind.get()
        }
        labels = sorted(items, key=str.upper)
        self.elrs_product_combo["values"] = labels
        if self.elrs_product.get() not in items:
            self.elrs_product.set(labels[0] if labels else "")
        self._on_elrs_product_selected()

    def _on_elrs_product_selected(self):
        item = self.elrs_hardware.get(self.elrs_vendor.get(), {}).get(self.elrs_product.get())
        if not item:
            self.elrs_layout.set(self.elrs_vendor.get() + "：该类型下暂无型号")
            return
        envs = item["envs"]
        self.elrs_env_combo["values"] = envs
        if self.elrs_env.get() not in envs:
            self.elrs_env.set(envs[0] if envs else "")
        methods = ", ".join(item["upload_methods"]) or "-"
        text = (f"固件 {item['firmware']} | 布局 {item['layout'] or '-'} "
                f"| 上传 {methods} | 平台 {item['platform']}")
        if not envs:
            text = "当前源码无对应编译目标（旧款专属 target）：" + text
        self.elrs_layout.set(text)

    def _refresh_elrs_envs(self):
        envs = self.elrs_envs.get(self.elrs_kind.get(), [])
        self.elrs_env_combo["values"] = envs
        if self.elrs_env.get() not in envs:
            self.elrs_env.set(envs[0] if envs else "")

    def load_user_defines(self):
        source = Path(self.elrs_source.get().strip())
        if not elrs_backend.user_defines_path(source):
            self.write_log("提示：未找到 user_defines.txt，请检查 ELRS 源码目录。")
            return
        values = elrs_backend.read_user_defines(source)
        self.elrs_domain.set(str(values["domain"]))
        self.elrs_binding.set(str(values["binding"]))
        self.elrs_lock.set(bool(values["lock"]))
        self.elrs_defines_loaded = True
        self.write_log(f"user_defines: 频段={values['domain']}, 锁定={'开' if values['lock'] else '关'}")

    def _apply_user_defines(self) -> bool:
        source = Path(self.elrs_source.get().strip())
        values = {
            "domain": self.elrs_domain.get(),
            "binding": self.elrs_binding.get(),
            "lock": self.elrs_lock.get(),
        }
        ok, note = elrs_backend.write_user_defines(source, values)
        self.write_log(("user_defines 已保存。" if ok else "失败：") + note)
        return ok

    def save_user_defines(self):
        self.status.set("user_defines 已保存" if self._apply_user_defines()
                        else "user_defines 保存失败")

    def open_user_defines(self):
        import os

        source = Path(self.elrs_source.get().strip())
        path = elrs_backend.user_defines_path(source)
        if not path:
            messagebox.showerror("缺少文件", "未找到 user_defines.txt。")
            return
        self._apply_user_defines()
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))

    def open_elrs_artifact_dir(self):
        import os

        source = Path(self.elrs_source.get().strip())
        directory = elrs_backend.artifacts_dir(source, self.elrs_env.get())
        if (directory is None or not directory.is_dir()) and self.elrs_artifact.get():
            directory = Path(self.elrs_artifact.get()).parent
        if directory is None or not directory.is_dir():
            messagebox.showerror("目录不存在", "请先编译固件，或选择已编译过的目标。")
            return
        try:
            os.startfile(str(directory))
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))

    def load_elrs_targets(self):
        source = Path(self.elrs_source.get().strip())
        if not elrs_backend.find_source(source):
            messagebox.showerror("目录无效", "请选择包含 platformio.ini 的 ELRS 源码目录（根目录或 src 子目录）。")
            return

        def worker():
            self.events.put(("busy", "1"))
            self.events.put(("status", "正在读取 ELRS 编译目标..."))
            envs = elrs_backend.list_envs(source)
            self.events.put(("elrs_envs", json.dumps(envs)))
            count = sum(len(items) for items in envs.values())
            self.events.put(("log", f"ELRS 目标: TX {len(envs.get('TX', []))} / RX {len(envs.get('RX', []))}"))
            self.events.put(("busy", "0"))
            self.events.put(("status", f"已读取 {count} 个 ELRS 编译目标" if count
                             else "未读取到编译目标，请检查源码与 PlatformIO 安装"))

        self.run_async(worker)

    def _elrs_check_worker(self):
        self.events.put(("busy", "1"))
        source = Path(self.elrs_source.get().strip())
        ok = elrs_backend.check_environment(
            source, lambda line: self.events.put(("log", line))
        )
        self.events.put(("busy", "0"))
        self.events.put(("status", "ELRS 环境检查完成" if ok
                         else "ELRS 环境检查未通过，请查看日志"))

    def _elrs_install_worker(self):
        self.events.put(("busy", "1"))
        ok = elrs_backend.install_platformio(
            lambda line: self.events.put(("log", line))
        )
        self.events.put(("busy", "0"))
        self.events.put(("status", "PlatformIO 安装完成/已安装" if ok
                         else "PlatformIO 安装失败，请查看日志"))

    def _elrs_build_worker(
        self, source: Path, env: str, config: str | None = None,
        active_config: str | None = None,
    ):
        self.events.put(("busy", "1"))
        self.events.put(("progress_mode", "indeterminate"))
        ok, artifacts = elrs_backend.build_firmware(
            source, env, lambda line: self.events.put(("log", line)), config
        )
        for artifact in artifacts:
            self.events.put(("log", f"产物: {artifact}"))
        if artifacts:
            self.events.put(("elrs_artifact", str(artifacts[0])))
        if ok and active_config:
            applied = elrs_backend.apply_active_layout(source, env, active_config)
            if applied and applied.get("bin"):
                self.events.put(("log", f"已自动应用自定义硬件配置: {applied['bin']}"))
                self.events.put(("elrs_artifact", applied["bin"]))
            elif applied and applied.get("warning"):
                self.events.put(("log", f"自定义配置应用失败: {applied['warning']}"))
        self.events.put(("progress_mode", "determinate"))
        self.events.put(("busy", "0"))
        self.events.put(("status", "ELRS 固件编译完成" if ok
                         else "ELRS 编译失败，请查看日志"))

    def pick_source(self):
        if self.system.get() == "elrs":
            value = filedialog.askdirectory(title="选择 ELRS 源码目录")
            if value:
                self.elrs_source.set(value)
                self.elrs_defines_loaded = False
                self.load_user_defines()
                self.load_elrs_targets()
            return
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
        if self.busy:
            messagebox.showinfo("正在执行", "已有任务在运行，请等待完成后再操作。")
            return False
        self.busy = True

        def wrapper():
            try:
                fn()
            finally:
                self.events.put(("busy", "0"))

        threading.Thread(target=wrapper, daemon=True).start()
        return True

    def spinner_tick(self):
        if self.busy:
            frames = ("|", "/", "-", "\\")
            frame = frames[self.spinner_index % len(frames)]
            self.spinner_index += 1
            self.status.set(f"{self.status.get().split('  ')[0]}  {frame}")
        self.root.after(180, self.spinner_tick)

    def check_environment(self):
        if self.system.get() == "elrs":
            self.run_async(self._elrs_check_worker)
            return
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
        python = external_python()
        for module, package in REQUIRED_IMPORTS.items():
            check = f"import {module}" if "." not in module else f"import {module}"
            code = 1
            if python:
                code, _ = run_capture([python, "-c", check])
            lines.append(f"{'OK' if code == 0 else '缺失'} Python: {package}")
            if code != 0:
                command = f"{python} -m pip install {package}" if python else f"python -m pip install {package}"
                lines.append(f"  处理方法: 执行 {command}")
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
        if self.system.get() == "elrs":
            self.run_async(self._elrs_install_worker)
            return
        source = self.source_path()
        if not source:
            return
        requirements = source / "requirements.txt"
        if not requirements.is_file():
            messagebox.showerror("缺少文件", "源码目录中没有 requirements.txt。")
            return
        python = external_python()
        if not python:
            messagebox.showerror("缺少 Python", "未在 PATH 中找到 python，请先安装 Python 并重启本程序。")
            return
        if not messagebox.askyesno(
            "确认安装",
            f"将使用 {python} 安装 requirements.txt 中列出的 Python 依赖，是否继续？",
        ):
            return
        self.run_async(lambda: self._command_worker(
            [python, "-m", "pip", "install", "-r", str(requirements)], source,
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
        if self.system.get() == "elrs":
            env = self.elrs_env.get()
            source = Path(self.elrs_source.get().strip())
            product = self.elrs_hardware.get(self.elrs_vendor.get(), {}).get(
                self.elrs_product.get()
            )
            if product is not None and not product["envs"]:
                messagebox.showerror(
                    "目标无效",
                    "该型号属于旧款专属 target，当前 ELRS 源码没有对应编译目标；"
                    "请切换到手动模式或选择其他型号。",
                )
                return
            if not env:
                messagebox.showerror("目标无效", "请先点击读取编译目标，并选择一个编译目标。")
                return
            if not self._apply_user_defines():
                messagebox.showerror("配置失败", "user_defines 保存失败，已取消编译。")
                return
            config = product["config"] if product else None
            active = app_settings.active_layout()
            active_config = (
                active.get("config") if active and product
                and active.get("config") == product["config"] else None
            )
            self.run_async(lambda: self._elrs_build_worker(
                source, env, config, active_config
            ))
            return
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
        if code != 0:
            missing_modules = sorted(set(re.findall(
                r"ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]",
                output,
            )))
            if missing_modules:
                packages = []
                for module in missing_modules:
                    package = PYTHON_PACKAGE_ALIASES.get(
                        module.split(".", 1)[0], module.split(".", 1)[0]
                    )
                    if package not in packages:
                        packages.append(package)
                requirements = Path(self.source.get().strip()) / "requirements.txt"
                self.events.put((
                    "log",
                    "检测到缺少 Python 依赖："
                    f"{', '.join(packages)}\n"
                    f"请打开：{requirements}\n"
                    f"添加一行：{chr(10).join(packages)}\n"
                    "保存后，点击“安装 Python 依赖”。",
                ))
                result = f"失败：缺少 Python 依赖 {', '.join(packages)}"
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
                elif kind == "progress_mode":
                    if value == "indeterminate":
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=0)
                elif kind == "elrs_artifact":
                    self.elrs_artifact.set(value)
                elif kind == "elrs_envs":
                    self.elrs_envs = json.loads(value)
                    self._refresh_elrs_envs()
                    self._load_hardware_catalog()
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
