# Open Radio Builder

Windows 桌面构建前端：用图形界面编译 **EdgeTX** 与 **ExpressLRS** 固件。本目录不是固件源码，也不修改源码，只读取源码里的板型 / 编译目标信息，并代为调用 CMake、PlatformIO。

## 文件一览

| 文件 | 作用 |
| --- | --- |
| `open_radio_builder.py` | 主程序：Tkinter 界面、编译选项、构建调度 |
| `edgetx_backend.py` | EdgeTX 源码解析与工具链检查（无 UI 依赖） |
| `elrs_backend.py` | ExpressLRS / PlatformIO 后端，可独立命令行运行 |
| `app_settings.py` | 工作区、虚拟环境、输出目录与用户设置 |
| `builder_utils.py` | 子进程封装、Windows 中文编码处理 |
| `OpenRadioBuilder.spec` | PyInstaller 单文件打包配置 |
| `EdgeTX_Windows11_VSCode_T20编译调试记录.md` | Windows + T20 / V16 构建排错记录 |
| `.codebuddy/spec/add_elrs_plan.md` | ELRS 集成方案（描述旧版单文件结构，仅供追溯） |

## 启动方式

| 方式 | 命令 / 位置 |
| --- | --- |
| 直接运行 exe | `dist\OpenRadioBuilder.exe`（打包后生成） |
| 源码运行 | `python open_radio_builder.py` |
| 重新打包 exe | `pyinstaller --noconfirm --clean OpenRadioBuilder.spec` |
| 后端命令行 | `python elrs_backend.py check\|install\|list\|build\|hardware\|layout\|defines\|packages\|prefetch [--source <目录>]` |

打包环境为 Python 3.14 + PyInstaller 6.x，产物是单文件、无控制台、带图标的 exe（约 12 MB）。命令行后端与图形界面共用同一套后端函数，可用 `check` / `hardware` 快速验证环境。

## 首次启动：源码目录从哪来

启动时会按顺序确定顶部「源码目录」输入框的值：

1. `%APPDATA%\OpenRadioBuilder\settings.json` 中上次保存的 `edgetx_source` / `elrs_source`（目录仍存在就直接用）；
2. 没有保存值就在工程附近自动寻找标准 GitHub checkout：EdgeTX 认根目录的 `CMakeLists.txt` + `radio/src/CMakeLists.txt`，ELRS 认 `src/platformio.ini`；
3. 都找不到就留空，点右侧「浏览」选一次。

选过（或直接粘贴到输入框）的有效目录会立即写入设置，下次打开自动回填。识别前提是 **GitHub 拉下来的标准目录结构**，非标准布局不支持。

> 例：本机源码 `F:\mcu_code\EDGETX\edgetx`、`F:\mcu_code\ELRS\ExpressLRS` 由第 2 步自动探测到；一旦在界面里选过别的目录，以后就以最后选择的为准。

## 界面总览

顶部是 `EdgeTX 固件` / `ExpressLRS` 切换、源码目录 + 「浏览」、VS Developer PowerShell 脚本（仅 EdgeTX）、`路径设置`；底部「构建操作」区含日志、进度条和状态栏，按钮随所选系统变化：

| 按钮 | EdgeTX | ExpressLRS |
| --- | --- | --- |
| 检查环境 | 查工具链、子模块、Python 包、libclang，并顺带读取发射机型号 | 查 Python、`platformio.ini`、PlatformIO、平台包 |
| 安装 Python 依赖 | 按源码 `requirements.txt` 安装 | 变为「安装 PlatformIO」，装到独立 venv |
| 预下载平台包 | 隐藏 | 提前下载平台与依赖 |
| 配置 CMake | 生成并执行 CMake 配置命令 | 隐藏 |
| 编译固件 | 先配置 ARM 子工程，再编 `firmware` | `pio run -e <目标>` |
| 清除日志 | 有 | 有 |

所有编译都在工作线程执行，界面经队列刷新，不会卡死。

---

## 流程 A：编译 EdgeTX 固件

**前提**（装一次即可）：Visual Studio 2022（提供 `cl.exe` 与 LLVM/libclang 20+）、CMake、Ninja、ARM GNU Toolchain、Python + Pillow / lz4 / pyelftools、Git 子模块已初始化。只编固件不需要 Qt / SDL / NSIS。

1. 顶部选 `EdgeTX 固件`。
2. 确认「EdgeTX 源码目录」；必要时在第二行「浏览」选择 VS Developer PowerShell 脚本，程序会用它包住 CMake 命令。
3. 点 `检查环境`。日志逐项列出工具、子模块、Python 包、libclang，缺什么按提示修；Python 依赖可点 `安装 Python 依赖`。此步也会自动调用「获取发射机型号」。
4. 点 `获取发射机型号`，在「目标配置」里依次选 **厂商 → 型号 → 类型(Release/Debug) → 语言 → 手型设置**；「目录」默认 `<源码>/build/<目标>`，一般不用改。右侧的协议 / 模块勾选会按该型号自动带出默认值。
5. 按需勾选「常用功能」「协议支持」「模块支持」，在「硬件配置」里设置 FS 霍尔摇杆、蓝牙模块、内置 GPS、摇杆死区、摇杆类型。若某型号蓝牙与 FS 霍尔摇杆共用同一个 UART，同时开启会被拦截提示。
6. 点 `配置 CMake`：拼出并执行 `cmake --fresh -S <源码> -B <构建目录> -G Ninja -DPCB=... -DPCBREV=... ...`，其中主机编译器固定为 `cl.exe`。
7. 点 `编译固件`：先 `cmake --build <构建目录> --target arm-none-eabi-configure`，再 `cmake --build <构建目录>/arm-none-eabi --target firmware --parallel`。
8. 产物：`<源码>/build/<目标>/arm-none-eabi/firmware.bin`；日志出现「编译成功了，固件已生成」即可刷机。

改过编译选项后，建议清空构建目录，再从第 6 步重新配置。

---

## 流程 B：编译 ExpressLRS 固件

**前提**：只需要 Python；PlatformIO 由工具装到独立 `pio-venv`，不污染系统 Python，也不需要 VSCode / MSVC / Ninja / ARM GCC。首次编译需联网下载数百 MB 工具链。

1. 顶部选 `ExpressLRS`，确认「ELRS 源码目录」。
2. 点 `检查环境`；若按钮显示 `安装 PlatformIO`，点一次完成安装。可选点 `预下载平台包`，勾选缺失平台提前下载，避免编译时干等。
3. 点 `读取编译目标 / 型号`：用 `pio project config --json-output` 列出 TX / RX 编译目标，并读 `src/hardware/targets.json` 生成 **厂商 → 型号** 目录。
4. 在 ELRS 面板依次选 **设备类型(TX/RX) → 厂商 → 型号 → 编译目标**。选型号后下方显示固件、layout、上传方式、平台；显示「当前源码无对应编译目标」表示是旧款专属 target。厂商选 `（手动选择目标）` 可跳过目录，直接按 TX / RX 选 env。
5. 配置 `user_defines 常用配置`：频段、绑定短语、首次连接锁定，点 `保存 user_defines`，也可 `打开 user_defines.txt` 手动改。写入前会备份 `.bak`，按整行精确替换；点「编译固件」时也会自动应用一次。
6. 可选：点 `查看硬件配置`，双击表格行编辑数值，可填 Lua 设备名、勾选「RX 当 TX」；保存后生成配置副本到 `config_overrides/`，若已有编译好的固件还会生成 `output/*.custom.bin`，并设为活动配置（之后每次编译自动应用，原 layout 文件和原固件都不改动）。
7. 点 `编译固件`：执行 `pio run -e <目标>`，日志按下载 / 依赖 / 编译 / 链接阶段刷新状态。
8. 点 `打开产物目录` 查看 `firmware.bin` / `.elrs` / `.hex`；启用活动配置时，最终刷写的是 `output/*.custom.bin`（界面「产物」标签会指向它）。

厂商 / 型号为空时，先确认源码目录是标准 GitHub checkout 且 `src/hardware/targets.json` 存在，再重做第 3 步。

---

## 路径设置与产物位置

点顶部 `路径设置` 打开「数据目录设置」，可查看 / 修改工作目录并勾选自定义 PlatformIO 缓存（改动后可能需要重新下载工具链）。

| 内容 | 默认位置 |
| --- | --- |
| 设置文件 | `%APPDATA%\OpenRadioBuilder\settings.json` |
| 数据工作目录 | 开发时为本工程目录；exe 且未配置时为 `%LOCALAPPDATA%\OpenRadioBuilder` |
| PlatformIO 虚拟环境 | `<工作目录>\pio-venv` |
| 配置副本 | `<工作目录>\config_overrides\*.layout.json` |
| 自定义固件副本 | `<工作目录>\output\*.custom.bin` |
| ELRS 原始产物 | `<ELRS源码>\src\.pio\build\<env>\firmware.bin` |
| EdgeTX 产物 | `<EdgeTX源码>\build\<目标>\arm-none-eabi\firmware.bin` |

`tools/`、`config_overrides/`、`output/`、`build/`、`dist/`、`*.exe` 等均被 `.gitignore` 排除，不会进版本库。

## 常见问题

- **缺 `cl.exe` / libclang**：顶层 CMake 是 Windows Superbuild，主机目标必须用 MSVC 的 `cl.exe`，ARM 子工程才用 ARM GCC；不要改用 Clang 链接主机目标。LLVM/libclang 需 20 或更高。
- **子模块未完成**：在 EdgeTX 源码目录执行 `git submodule update --init --recursive`。
- **首次 ELRS 编译很久**：PlatformIO 要联网下载平台、工具链和库，可先用 `预下载平台包`。
- **Python 3.15 生成 BIN 失败**：3.15 默认 UTF-8 模式会让 `elf2bin.py` 在中文 Windows 解码失败；工具已自动设置 `PYTHONUTF8=0`，仍建议用 3.14 及以下。
- **改了选项但固件没变**：清空构建目录后重新「配置 CMake」再编译。
- **只编固件**：不需要 Qt、SDL、NSIS，它们只用于 Companion / 模拟器。

更多 Windows 实战排错见 `EdgeTX_Windows11_VSCode_T20编译调试记录.md`。
