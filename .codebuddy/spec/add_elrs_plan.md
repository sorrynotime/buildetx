# 集成 ExpressLRS 编译链方案（add_elrs，v2：模块化拆分）

## 1. 目标

把单文件 EdgeTX Builder 拆成「UI + EdgeTX 后端 + ELRS 后端」结构，新增 ExpressLRS 编译能力：
选择 ELRS 源码目录 → 检查/安装 PlatformIO → 选择 TX/RX 目标 → 配置 user_defines → 编译 → 得到固件。
ELRS 后端可脱离 UI 用命令行独立测试；不破坏现有 EdgeTX 流程；exe 仍无第三方依赖打包。

## 2. 已确认的现状依据

- 本机 ELRS 源码：`E:\Code\ExpressLRS`，4.1.0-185-gf8700cab；`platformio.ini` 在 `src/` 子目录，`src/targets/*.ini` 按平台拆分（esp32/esp32s3/esp32c3/esp8285 × tx/rx），经 `extra_configs` 引入。
- ELRS 功能开关：`src/user_defines.txt`，注释/取消注释 `-D` 行（Regulatory_Domain、MY_BINDING_PHRASE、LOCK_ON_FIRST_CONNECTION 等）。
- 本机 Python 3.14.7 + pip 26.2.1 可用；未安装 PlatformIO。
- ELRS 工具链由 PlatformIO 首次编译自动下载，**不需要** VSCode/MSVC/libclang/Ninja/ARM GCC。
- 现有 `edgetx_builder.py`（1615 行）：源码解析与环境检测为纯函数（23~273 行），`App` 类混合 UI 与构建逻辑；`_command_worker`（1536 行起）强制 PowerShell + VS 前缀；`_build_ui`（905 行起）上半部分 EdgeTX 专属、下半部分构建操作区可复用；异步 queue + `_drain_events` 与后端无关。

## 3. 文件结构（按用户提议）

```
buildetx-s/
├─ edgetx_builder.py    # 入口 + App UI：两个系统切换按钮、共享构建操作区（日志/进度/状态）
├─ builder_utils.py     # 公共小模块：run_capture、_decode、external_python、resource_path
├─ edgetx_backend.py    # 现有 EdgeTX 纯函数原样迁入：read_target_catalog、read_cmake_options、
│                       #   find_compatible_libclang、read_common_targets 等 + 环境检查/CMake 命令生成
└─ elrs_backend.py      # 新增，纯标准库、不 import tkinter；带 __main__ CLI 可独立测试
```

关键约束：
- 后端不 import UI；日志/进度通过回调（如 `on_line(line)`）注入，UI 与 CLI 各自提供实现。
- `builder_utils.py` 独立抽出是为避免 ui ↔ backend 循环导入；内容纯移动不改逻辑。
- 入口仍是 `edgetx_builder.py`，PyInstaller spec 不用改（同目录模块自动打包进单文件 exe）。

## 4. UI 切换形式

主窗口顶部放两个切换按钮（EdgeTX 固件 / ExpressLRS，或等价分段控件）：
- 上半部分准备两个面板 Frame，切换时 `grid()` / `grid_remove()` 互换；EdgeTX 面板为现有内容，ELRS 面板含源码目录、TX/RX 与目标 env 下拉、（M3 起）user_defines 区。
- 下半部分"构建操作"区（按钮、进度、日志）共享，按钮动作按当前系统分发到对应后端。
- ELRS 模式下 VS 脚本行禁用，不查 MSVC/libclang。

## 5. 分步实施（顺序：拆分 → ELRS UI → ELRS 后端 → 接线的）

### M0：结构拆分，行为不变（纯重构）
- 抽出 `builder_utils.py`、`edgetx_backend.py`，现有函数原样移动；`edgetx_builder.py` 改为 import 使用。
- 不改任何构建逻辑与 UI 表现。
- 验收：`python edgetx_builder.py` 启动正常；EdgeTX 检查环境/读取目标/配置 CMake/编译与拆分前一致。

### M1：ELRS UI 先行（两个切换按钮 + ELRS 面板，后端用占位实现）
- 实现第 4 节切换：顶部 EdgeTX / ExpressLRS 两个按钮，上半部分面板互换，下半部分构建区共享。
- ELRS 面板内容：源码目录选择、TX/RX 类型与目标 env 下拉、检查环境 / 安装 PlatformIO / 读取目标 / 编译按钮、（预留）user_defines 区。
- **先冻结后端接口**（UI 只面向接口编程）：`check_environment(source, on_line)`、`install_platformio(on_line)`、`list_envs(source) -> {TX: [...], RX: [...]}`、`build(source, env, on_line)` 等，签名与回调形式在此步定死。
- 本步用 stub：返回模拟 env 列表、按钮提示"后端未接入"，仅供界面与交互走查；不接真实 pio。
- 验收：两个系统切换显示正确、布局不串；EdgeTX 全流程回归通过；ELRS 各按钮有明确占位反馈。

### M2：`elrs_backend.py` 独立后端 + CLI（不碰 UI）
- 按 M1 冻结的接口实现：源码目录校验（`src/platformio.ini`，兼容根目录旧布局）；环境检查（python/git/pio 版本）；一键安装 PlatformIO（固定目录 venv，默认 `<工具目录>\tools\pio-venv`，先 `python -m venv` 再 pip 安装，不污染系统 Python，装好不移动）；调用统一走 `<venv>\Scripts\python.exe -m platformio`（无 venv 回退系统 python），不依赖 pio 进 PATH。
- 目标枚举：`pio project config --json-output`（cwd=src）解析 `env:*`，按 TX/RX 与平台分类，过滤 native 等测试 env。
- 编译：`pio run -e <env>`（cwd=src），流式输出回调；产物定位 `.pio/build/<env>/firmware.bin|.elrs`。
- CLI 形态（示例）：`python elrs_backend.py check`、`install`、`list --source E:\Code\ExpressLRS`、`build --source ... --env <env>`。
- 验收：全程不开 UI 完成 检查→安装→枚举→编译出一个 TX 固件；首次下载工具链有明确进度/提示。

### M3：UI 接真实后端并联调
- 把 M1 的 stub 替换为 `elrs_backend` 真实调用，复用 queue + `_drain_events`。
- `_command_worker` 增加后端开关：ELRS 不叠加 VS 前缀；进度条在 ELRS 构建时用不确定模式 + spinner。
- 验收：UI 内完成 M2 全流程，产物路径写入日志；EdgeTX 模式回归通过。

### M4：user_defines 常用配置 GUI
- 读取 `src/user_defines.txt`：Regulatory_Domain 频段下拉（互斥）、MY_BINDING_PHRASE 文本、LOCK_ON_FIRST_CONNECTION 等常用布尔开关；其余不动。
- 写入：整行精确替换（注释/取消注释），不全文件重写；先备份 `user_defines.txt.bak`；构建前自动保存；另提供"打开文件"按钮。
- 读写逻辑放 `elrs_backend.py` 并配 CLI 自测，UI 只做展示。
- 验收：仅目标行变化、备份生成、产物反映新配置。

### M5（可选，另行确认）
- 产物打开目录/复制、ELRS 选择持久化、串口刷写（`pio run -t upload`，需硬件另评估）、exe 重新打包验证。

## 6. 风险与注意

- M0 是大范围移动，坚持"纯移动不改逻辑"，先回归再进 M1；git 小步提交。
- 首次 pio 构建联网下载工具链（数百 MB），CLI 与 UI 都要有明确等待提示；网络/代理问题只给指引。
- pio 数据目录默认 `C:\Users\<用户>\.platformio`（数 GB），可用 `PLATFORMIO_CORE_DIR` 改盘，工具暂不代管。
- 所有 pio 调用在 UI 中走工作线程，GUI 只经 queue 更新。
- user_defines 属于用户源码修改：备份 + 精确行替换。
- 不引入第三方 Python 依赖；PlatformIO 装在外部 venv，不随 exe 打包。
