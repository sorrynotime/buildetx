# EdgeTX Windows 11 + VS Code 编译调试记录

## 1. 目标与结果

目标是在 Windows 11、VS Code 环境下编译 EdgeTX，并确认 T20 的 FlySky（FS）数字霍尔摇杆编译条件。

最终结果：T20 固件编译成功，使用的是 EdgeTX 主线提交 `298e6e6`。

生成文件：

- `E:\Code\edgetx\build-t20\arm-none-eabi\firmware.bin`
- `E:\Code\edgetx\build-t20\arm-none-eabi\bootloader.bin`
- `firmware.elf`、`firmware.map`、`firmware.lst`

## 2. 使用的工具

### 编辑器与插件

- Visual Studio Code
- C/C++（Microsoft）
- CMake Tools
- CMake Language Support
- clangd（可选）
- Cortex-Debug（可选，仅用于 ST-Link/J-Link 调试）

### Windows 工具链

- Visual Studio 2026 Community / MSVC v143 兼容构建工具
- C++ Clang 编译器
- Windows 11 SDK
- CMake `4.4.2`
- Ninja `1.13.2`
- ARM GNU Toolchain `14.2.Rel1`，提供 `arm-none-eabi-gcc 14.2.1`
- Git `2.55.0`
- Python `3.14.7`

Visual Studio 需要通过 Developer PowerShell 加载环境：

```powershell
& "D:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\Launch-VsDevShell.ps1" `
  -Arch amd64 -HostArch amd64
```

## 3. 源码准备

源码目录：`E:\Code\edgetx`

初始化全部子模块（包括嵌套子模块）：

```powershell
cd E:\Code\edgetx
git submodule update --init --recursive
```

## 4. Python 构建依赖

EdgeTX 的字体和位图生成脚本依赖 Pillow、lz4 等 Python 包。一次性安装源码清单中的依赖：

```powershell
cd E:\Code\edgetx
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

验证：

```powershell
python -c "from PIL import Image; import lz4.block; print('Python build dependencies OK')"
```

## 5. 编译器环境设置

顶层 CMake 是 Windows Superbuild，需要主机编译器；固件子工程随后自动使用 ARM GCC。

```powershell
$env:CC = "cl.exe"
$env:CXX = "cl.exe"
```

确认使用 64 位 MSVC：

```powershell
where.exe cl
where.exe cmake
where.exe ninja
where.exe arm-none-eabi-gcc
```

## 6. T20 编译命令

### 顶层配置

```powershell
cmake --fresh -S . -B build-t20 -G Ninja `
  -DPCB=X7 `
  -DPCBREV=T20 `
  -DTRANSLATIONS=CN `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_C_COMPILER=cl.exe `
  -DCMAKE_CXX_COMPILER=cl.exe
```

### 配置 ARM 固件子工程

```powershell
cmake --build build-t20 --target arm-none-eabi-configure
```

### 编译固件

```powershell
cmake --build build-t20\arm-none-eabi --target firmware --parallel
```

## 7. 调试过程中遇到的问题

### 问题一：找不到 C/C++ 编译器

错误：`No CMAKE_C_COMPILER could be found`。

原因：ARM GCC 不是顶层 Superbuild 使用的主机编译器，当前终端也没有加载 Visual Studio 编译环境。

解决：打开 Developer PowerShell，加载 VS 环境，并设置 `CC/CXX` 为 `cl.exe`：

```powershell
& "D:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\Launch-VsDevShell.ps1" -Arch amd64 -HostArch amd64
$env:CC = "cl.exe"
$env:CXX = "cl.exe"
```

### 问题二：Clang 链接失败

错误：`undefined symbol: mainCRTStartup`，同时命令中出现 `-nostartfiles -nostdlib`。

原因：Clang 主机链接环境没有正确接入 Windows CRT。

解决：改用已加载 VS 环境的 MSVC `cl.exe` 作为顶层 C/C++ 编译器；ARM 固件仍使用 ARM GCC。

### 问题三：缺少 Pillow

错误：`ModuleNotFoundError: No module named 'PIL'`。

解决：安装完整 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

### 问题四：缺少 lz4

错误：`ModuleNotFoundError: No module named 'lz4'`。

原因：只安装了 Pillow，没有安装 EdgeTX 的完整 Python 依赖。

解决：同样执行：

```powershell
python -m pip install -r requirements.txt
```

### 问题五：LTO 串行编译提示

提示：`lto-wrapper.exe: warning: using serial compilation of 2 LTRANS jobs`。

这是 GCC LTO 的性能提示，不是编译错误，不影响固件生成。

## 8. FS 霍尔摇杆配置确认

T20 使用：

```text
-DPCB=X7 -DPCBREV=T20
```

在 `radio/src/targets/taranis/CMakeLists.txt` 的 T20 分支中启用：

```cmake
set(FLYSKY_GIMBAL ON)
add_definitions(-DRADIO_FAMILY_T20)
```

公共 CMake 根据 `FLYSKY_GIMBAL` 编译 `flysky_gimbal_driver.cpp` 并定义 `-DFLYSKY_GIMBAL`。T20 的 HAL 使用 UART4、PA0/PA1 和 DMA1，运行时会先探测数字霍尔摇杆，失败时回退到模拟摇杆。

## 9. V16 编译方式

V16 使用独立构建目录：

```powershell
cmake --fresh -S . -B build-v16 -G Ninja `
  -DPCB=X10 `
  -DPCBREV=V16 `
  -DTRANSLATIONS=CN `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_C_COMPILER=cl.exe `
  -DCMAKE_CXX_COMPILER=cl.exe

cmake --build build-v16 --target arm-none-eabi-configure
cmake --build build-v16\arm-none-eabi --target firmware --parallel
```

V16 的 CMake 分支同样启用 `FLYSKY_GIMBAL`，但目标是 `PCB=X10`、`PCBREV=V16`。

## 10. 排错顺序

建议按以下顺序检查：

1. `git submodule status --recursive`
2. `cmake --version`、`ninja --version`
3. `arm-none-eabi-gcc --version`
4. `python --version`
5. `python -m pip install -r requirements.txt`
6. 在 Developer PowerShell 中确认 `where.exe cl`
7. 删除或使用新的构建目录，重新执行 CMake 配置
8. 先执行 `arm-none-eabi-configure`，再执行 `firmware`

## 11. 不需要提前安装的工具

只编译 T20/V16 固件时，不需要先安装 Qt、SDL、NSIS 或 Companion 专用依赖。它们只在编译 Companion、模拟器或安装包时需要。
