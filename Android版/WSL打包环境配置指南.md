# 课堂派 Android 端 — WSL 打包环境配置指南

> 本文档记录在 WSL2 (Ubuntu 24.04) 中从零搭建 Buildozer 打包环境、一直到成功生成 APK 的完整流程，以及过程中遇到的每一个问题和解决方案。换环境打包时按本文档操作即可。

---

## 目录

1. [前置条件](#1-前置条件)
2. [WSL 环境初始化](#2-wsl-环境初始化)
3. [项目文件同步](#3-项目文件同步)
4. [buildozer.spec 配置说明](#4-buildozerspec-配置说明)
5. [首次构建全流程](#5-首次构建全流程)
6. [每次遇到的所有问题及解决方案](#6-遇到的所有问题及解决方案)
7. [后续构建（增量）](#7-后续构建增量)
8. [项目结构参考](#8-项目结构参考)
9. [Bug 修复记录：互动课件下载失败](#9-bug-修复记录互动课件下载失败)
10. [构建产物](#10-构建产物)

---

## 1. 前置条件

| 条件 | 说明 |
|------|------|
| Windows 10/11 | 启用 WSL2 |
| WSL 发行版 | Ubuntu 24.04 LTS |
| 磁盘空间 | 至少 **10GB**（首次构建会下载 NDK/SDK/Gradle，约 3GB 缓存） |
| 网络 | 首次需要下载大量组件，**建议配置代理或预下载 Gradle zip** |

---

## 2. WSL 环境初始化

### 2.1 安装系统依赖

```bash
sudo apt update && sudo apt install -y \
  openjdk-17-jdk \
  python3-dev \
  python3-pip \
  python3-venv \
  autoconf \
  automake \
  libtool \
  libltdl-dev \
  libncurses-dev \
  unzip \
  zip \
  git \
  build-essential \
  bzip2 \
  gzip
```

### 2.2 安装 buildozer 和 Cython

> **关键问题**：Ubuntu 24.04 启用了 PEP 668（`externally-managed-environment`），普通 `pip install` 会被拒绝。每次 pip 命令必须加环境变量。

```bash
# 永久写入 ~/.bashrc
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
echo 'export PIP_BREAK_SYSTEM_PACKAGES=1' >> ~/.bashrc
source ~/.bashrc

# 安装
pip install buildozer Cython
```

验证安装：

```bash
buildozer --version   # 应输出 1.6.0+
cython --version      # 应输出 0.29.37+
javac --version       # 应输出 17.x
```

### 2.3 可选：安装 ccache（加速后续编译）

```bash
sudo apt install -y ccache
```

---

## 3. 项目文件同步

项目代码需要放在 WSL 文件系统内（不要用 `/mnt/e/...`），否则符号链接和文件权限会导致构建失败。

```bash
# 在 WSL 中
mkdir -p ~/ktp/src/ktp_core
```

需要放入 `~/ktp/` 的文件：

| 文件 | 说明 |
|------|------|
| `main.py` | Kivy 应用主入口（在项目根目录） |
| `buildozer.spec` | Buildozer 配置文件 |
| `requirements.txt` | Python 依赖 |
| `DroidSansFallback.ttf` | 中文字体文件 |
| `src/ktp_core/__init__.py` | 包初始化 |
| `src/ktp_core/client.py` | 课堂派 API 客户端 |
| `src/ktp_core/constants.py` | 常量定义 |
| `src/ktp_core/download_manager.py` | 下载管理器（含互动课件PDF回退） |
| `src/ktp_core/encrypt.py` | 加密工具 |
| `src/ktp_core/url_resolve.py` | URL 解析 |
| `src/ktp_core/_pyaes/` | 纯 Python AES 实现（Android 替代 pycryptodome） |
| `cacert.pem` | CA 证书包（Android SSL 修复） |

> **提示**：如果文件在 Windows 侧编辑，通过 `//wsl.localhost/Ubuntu-24.04/home/<user>/ktp/` 路径复制即可。
>
> ```cmd
> REM 从 Windows 同步到 WSL
> copy "E:\项目路径\src\ktp_core\download_manager.py" "\\wsl.localhost\Ubuntu-24.04\home\<user>\ktp\src\ktp_core\download_manager.py"
> ```

---

## 4. buildozer.spec 配置说明

```ini
[app]
title = 课堂派资料下载
package.name = ktp_downloader
package.domain = com.ktp
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.1.0

# Python 依赖（buildozer 会自动用 pip 安装到 APK 中）
# ★ kivy 和 Pillow 必须用精确版本号 ==，不能用 >=，否则 p4a 不会激活 recipe
requirements = python3,kivy==2.3.0,requests>=2.28.0,Pillow==8.4.0

orientation = portrait
fullscreen = 1
android.permissions = INTERNET

# API 级别
android.api = 34          # 编译 SDK 版本
android.minapi = 26       # 最低支持的 Android 版本 (8.0)
android.ndk = 25b         # NDK 版本
android.arch = arm64-v8a  # 目标架构（现代手机都是 arm64）

p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 1

# ★ 关键配置：指定本地 p4a 路径，避免每次 git clone
p4a.source_dir = /home/<user>/ktp/.buildozer/android/platform/python-for-android
```

---

## 5. 首次构建全流程

```bash
cd ~/ktp
buildozer android debug
```

首次构建会依次执行：

| 阶段 | 说明 | 耗时 |
|------|------|------|
| 下载 p4a（python-for-android） | 克隆到 `.buildozer/android/platform/` | ~2 分钟 |
| 下载 Android SDK/NDK | 约 1.5 GB | 取决于网速 |
| 下载各 recipe 源码 | Python 3.11.5、OpenSSL、SDL2、Freetype 等 | ~3 分钟 |
| 编译 hostpython3 | x86 版 Python（用于交叉编译） | ~2 分钟 |
| 编译 NDK 原生库 | libffi、OpenSSL、Python3 for arm64、SDL2 + 子库 | **30-50 分钟** |
| pip 安装 Python 模块 | Kivy、Pillow、requests → arm64 | ~3 分钟 |
| Gradle 编译 Java → APK | assembleDebug + 签名 | ~3 分钟 |

**首次构建总耗时：约 40-60 分钟**（取决于 CPU 核心数和网速）

---

## 6. 遇到的所有问题及解决方案

### 问题 1：PEP 668 — pip 拒绝安装

**现象**：

```
error: externally-managed-environment
× This environment is externally managed
```

**原因**：Ubuntu 24.04 默认启用 PEP 668，禁止系统级 pip 安装。

**解决**：

```bash
# 方法一：设置环境变量（推荐，已写入 2.2 节）
export PIP_BREAK_SYSTEM_PACKAGES=1

# 方法二：加 --break-system-packages 参数
pip install --break-system-packages buildozer Cython
```

每次执行 `buildozer` 也需要该变量，因为它内部会调用 pip：

```bash
PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug
```

---

### 问题 2：buildozer: command not found

**现象**：

```bash
bash: buildozer: command not found
```

**原因**：pip 安装的可执行文件在 `~/.local/bin`，不在默认 PATH 中。

**解决**：

```bash
export PATH="$HOME/.local/bin:$PATH"
# 永久生效写入 ~/.bashrc
```

---

### 问题 3：pyconfig.h 缺失 — Python3 编译报错

**现象**：

```
fatal error: 'pyconfig.h' file not found
#include "pyconfig.h"
         ^~~~~~~~~~~~
```

同时 p4a 输出显示：

```
[INFO]:    python3 said it is already built, skipping
```

**原因**：上次构建中途被 kill，Python3 recipe 的状态文件被标记为"已构建"，但 `pyconfig.h` 没有被正确复制到 `Include/` 目录。该文件实际存在于 `android-build/pyconfig.h`，但 NDK 编译器在 `Include/` 中查找。

**解决**（手动复制）：

```bash
SRC=~/ktp/.buildozer/android/platform/build-arm64-v8a/build/other_builds/python3/arm64-v8a__ndk_target_26/python3

cp "$SRC/android-build/pyconfig.h" "$SRC/Include/pyconfig.h"
```

然后重新运行 `buildozer android debug`。

---

### 问题 4：Gradle 下载卡住（国内网络）

**现象**：输出停在：

```
[DEBUG]:   -> running gradlew clean assembleDebug
[DEBUG]:   Downloading https://services.gradle.org/distributions/gradle-8.0.2-all.zip
```

一直不动，最终超时失败。

**原因**：`services.gradle.org` 在国内访问极慢甚至不通，gradle-8.0.2-all.zip 约 120MB，无法自动下载。

**解决**（手动放置 Gradle zip）：

**第一步**：找到 Gradle 缓存目录中的 hash 文件夹：

```bash
ls ~/.gradle/wrapper/dists/gradle-8.0.2-all/
# 输出类似：14bt34ptcsg1ikmfn78tdh1keu
```

**第二步**：从镜像站或本机下载 `gradle-8.0.2-all.zip`，放到该 hash 目录下。

> 国内镜像：`https://mirrors.cloud.tencent.com/gradle/gradle-8.0.2-all.zip`

```bash
# 假设 zip 已下载到 Windows 侧 e:\某路径\
# 从 Windows 复制到 WSL
HASH_DIR=~/.gradle/wrapper/dists/gradle-8.0.2-all/14bt34ptcsg1ikmfn78tdh1keu
rm -f "$HASH_DIR/gradle-8.0.2-all.zip.part"   # 删除不完整的下载
rm -f "$HASH_DIR/gradle-8.0.2-all.zip.lck"    # 删除锁文件
cp /mnt/e/某路径/gradle-8.0.2-all.zip "$HASH_DIR/"
```

**第三步**：重新运行构建，Gradle 会自动解压并使用。

> **提示**：hash 目录名 `14bt34ptcsg1ikmfn78tdh1keu` 是 Gradle wrapper 根据 URL 计算出来的，不同版本不同。如果不确定，可以等第一次卡住后 `Ctrl+C`，再到 `~/.gradle/wrapper/dists/` 下查看目录结构，手动放 zip 后再重跑。

---

### 问题 5：p4a 每次重新 clone（网络慢）

**现象**：每次运行 `buildozer` 都会尝试 `git clone` python-for-android，在国内网络下很慢。

**解决**：在 `buildozer.spec` 中指定本地路径，首次 clone 后后续复用：

```ini
[buildozer]
p4a.source_dir = /home/<user>/ktp/.buildozer/android/platform/python-for-android
```

首次成功后 `.buildozer/` 目录约 2.7 GB（含 NDK、SDK、编译缓存），不要删除。

---

### 问题 6：构建中途被 kill 后重跑失败

**现象**：之前 `Ctrl+C` 或 kill 了构建进程，重跑时某些 recipe 显示 "already built" 但实际文件不完整。

**解决**：

```bash
# 删除 dist（强制重建 Python dist）
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/dists/ktp_downloader

# 如果问题持续，删除整个 build 目录（保留 downloads 和 SDK/NDK）
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/build
```

然后重新运行 `buildozer android debug`。下载的包和 NDK/SDK 不会被删除。

---

### 问题 7：WSL 中 `stty` 报错

**现象**：

```
stty: 'standard input': Inappropriate ioctl for device
```

**原因**：buildozer 的某些 git/patch 操作调用了 `stty`，在非交互式终端下会报这个警告。

**影响**：**无影响，可忽略**。这只是警告，不影响构建结果。

---

### 问题 8：磁盘空间不足

**现象**：构建中途失败，提示 "No space left on device"。

**排查**：

```bash
# 在 WSL 中
du -sh ~/.buildozer/
du -sh ~/ktp/.buildozer/
```

**解决**：

- `.buildozer/android/platform/android-ndk-r25b` — 约 1.5 GB，NDK 编译工具链，**删除后下次需重新下载**
- `.buildozer/android/platform/android-sdk` — 约 150 MB，Android SDK
- `~/.gradle/` — Gradle 缓存，约 200-500 MB
- 清理后至少保留 **5 GB 空闲空间**

---

### 问题 9：SD2_image 2.8.0 克隆 skcms 失败（Google 被墙）

**现象**：

```
fatal: unable to access 'https://skia.googlesource.com/skcms/':
Failed to connect to skia.googlesource.com port 443
fatal: clone of 'https://skia.googlesource.com/skcms' into submodule path
  '.../SDL2_image/external/libjxl/third_party/skcms' failed
```

**原因**：p4a 默认 SDL2_image 版本 2.8.0 新增了 `libjxl` 依赖，其子模块 `skcms` 托管在 Google 的 `skia.googlesource.com`，国内完全无法访问。**即使之前构建成功的缓存中已有该子模块，执行 `完全清理重建` 后会重新 clone 从而暴露此问题。**

**解决**：修改 p4a recipe 将 SD2_image 回退到 2.6.3（不含 libjxl/skcms 依赖）。

```bash
# 1. 删除已下载的 2.8.0 tarball
rm -f ~/ktp/.buildozer/android/platform/build-arm64-v8a/packages/sdl2_image/SDL2_image-2.8.0.tar.gz

# 2. 修改 p4a recipe 版本号
SDI_RECIPE=~/ktp/.buildozer/android/platform/python-for-android/pythonforandroid/recipes/sdl2_image/__init__.py
sed -i "s/version = '2\.8\.0'/version = '2.6.3'/" "$SDI_RECIPE"

# 验证修改
grep version "$SDI_RECIPE"
# 输出：version = '2.6.3'
```

然后重新运行构建即可。SDL2_image-2.6.3.tar.gz 从 GitHub 直下没问题。

> **注意**：每次执行"完全清理重建"后，如果 p4a 被重新 clone，需要**重新修改 recipe**。建议将修改后的 recipe 文件备份。

---

### 问题 10：APK 启动闪退 — Android SSL 证书缺失

**现象**：APK 安装到手机后，点击图标显示 Loading 然后立刻闪退，且 `/storage/emulated/0/ktp_crash.log` 不存在（说明 Python 解释器还没跑到崩溃捕获就挂了）。

**原因**：Android 系统没有系统级 CA 证书文件，`requests` 库默认查找 `SSL_CERT_FILE` 环境变量的路径，找不到则 SSL 连接失败导致 crash。Python crash 发生在原生层，连 Python 的 `try/except` 都来不及捕获。

**解决** — 三步修复：

**第一步**：`buildozer.spec` 的 `requirements` 添加 `certifi` 和 `openssl`：

```ini
requirements = python3,kivy>=2.3.0,requests>=2.28.0,Pillow>=10.0.0,openssl,certifi
```

**第二步**：`main.py` 在 `import kivy` 之前设置 `SSL_CERT_FILE`：

```python
# ── Android SSL 证书修复（必须在任何 SSL 使用之前）──
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
except Exception:
    pass
```

**第三步**：给 `build()` 方法包 try/except，让崩溃在屏幕上显示而不是直接闪退：

```python
class KtpApp(App):
    def build(self):
        try:
            _find_font()
            sm = ScreenManager(transition=SlideTransition())
            # ... 添加 screen ...
            return sm
        except Exception:
            from kivy.uix.label import Label
            return Label(text=f"启动失败:\n{traceback.format_exc()[:500]}",
                         font_size=sp(10))
```

---

### 问题 11：pyconfig.h 未自动复制 — Python3 .so 损坏导致闪退（深入分析）

**现象**：APK 安装后闪退，`ktp_crash.log` 不存在，`adb logcat | grep python` 显示 `libpython3.11.so` 加载失败。

**原因**：这是整个构建中最隐蔽的问题。p4a 编译 Python3 recipe 时生成 `pyconfig.h` 放入 `android-build/` 但**不会自动复制到 `Include/`**。NDK 编译 SDL2 bootstrap 的 `start.c` 时查找 `Include/pyconfig.h` 失败 → 项目在 `start.c` 报编译错误（问题 3）或用上次残缺缓存侥幸通过但生成了**损坏的 libpython3.11.so**。

**关键诊断方法**：

```bash
# 检查 pyconfig.h 是否在正确位置
SRC=~/ktp/.buildozer/android/platform/build-arm64-v8a/build/other_builds/python3/arm64-v8a__ndk_target_26/python3
ls "$SRC/Include/pyconfig.h" 2>/dev/null && echo "OK" || echo "MISSING! Need fix."
```

**解决** — 必须完全清理后重建：

```bash
# 1. 删除 dist（p4a 会认为是全新的构建）
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/dists/ktp_downloader

# 2. 删除 python3 编译缓存（强制重编 Python3）
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/build/other_builds/python3

# 3. 删除 SDL2 bootstrap 编译缓存（强制重编 SDL2 及 start.c）
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds

# 4. 删除 libs 收集目录（强制重新收集 .so 文件）
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/build/libs_collections

# 5. 手动复制 pyconfig.h 到 Include（预防性修复）
cp "$SRC/android-build/pyconfig.h" "$SRC/Include/pyconfig.h"

# 6. 重新构建
cd ~/ktp && PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug
```

> **总结**：如果 APK 闪退且没有任何 Python 日志输出，说明原生 .so 层已损坏。直接做上面的"4步清理 + 重建"即可。

---

### Bug 6：build 目录损坏（inode 与 / 根目录相同）

**现象**：构建似乎成功，但 APK 中 `private.tar` 只有 74KB，缺少所有第三方 Python 包（kivy/requests/Pillow），启动即闪退。

**原因**：`.buildozer/android/platform/build-arm64-v8a/build` 目录的 inode 变成了 2（和 `/` 根目录相同），p4a 的 pip install 阶段把包安装到了 WSL 根目录而非正确的 build 子路径中。最终打包时私有资源文件不完整。

**解决**：

```bash
# 删除整个 build-arm64-v8a 目录（SDK/NDK 会被重新 download）
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a
# 重新构建
cd ~/ktp && PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug
```

---

## 7. 后续构建（增量）

代码修改后再次打包：

```bash
cd ~/ktp
PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug
```

增量构建会跳过已编译的 recipe（hostpython3、libffi、openssl、python3、SDL2 等），仅重新打包 Python 源码和 Android APK。

**增量构建时间：约 3-5 分钟**。

### 仅修改了 Python 源码（不改依赖）时的加速方法

如果只是改了 `main.py` 或 `src/ktp_core/*.py`，不涉及新增 pip 依赖：

```bash
# 最快：只重建 APK（跳过大部重编译）
cd ~/ktp
PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug 2>&1
# 大部分 recipe 会显示 "already built, skipping"
```

### 强制完全重新编译

```bash
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/dists/ktp_downloader
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a/build
PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug
```

---

## 8. 项目结构参考

```
~/ktp/                              ← 项目根目录
├── main.py                         ← Kivy 应用主入口
├── buildozer.spec                  ← Buildozer 打包配置
├── requirements.txt                ← Python 依赖
├── DroidSansFallback.ttf           ← 中文字体
├── src/
│   └── ktp_core/
│       ├── __init__.py
│       ├── client.py               ← 课堂派 API 客户端（登录、列表、下载URL）
│       ├── constants.py            ← API 常量和 BASE URL
│       ├── download_manager.py     ← 下载管理器（多线程、回退PDF、★含Bug修复）
│       ├── encrypt.py              ← 密码加密
│       ├── url_resolve.py          ← document.ketangpai.com 代理链接解析
│       └── _pyaes/                 ← 纯 Python AES 库（Android 替代 pycryptodome）
│           ├── __init__.py
│           ├── aes.py
│           ├── blockfeeder.py
│           └── util.py
└── bin/                            ← 构建产物
    └── ktp_downloader-0.1.0-arm64-v8a-debug.apk
```

---

## 9. Bug 修复记录

### Bug 1：互动课件下载失败

**现象**：Android 端下载互动课件时无法正常下载，会自动转为 PDF。Windows 端此功能正常。

### 根因

`download_manager.py` 的 `_fetch_slide_urls()` 方法在调用 `PrestudyTaskApi/preStudyList` API 时，header 中 token 为空字符串 `""`，导致 API 认证失败、幻灯片 URL 获取失败，整个下载流程中断。

```python
# 修复前（有 Bug）
r = fallback_s.post(
    f"{BASE}//PrestudyTaskApi/preStudyList",
    headers=_headers(""),   # ← 空 token！
    data=_json_dumps(payload),
    timeout=60,
)
```

而项目文档明确说明"所有请求必须携带 `token` header"，且 `KetangpaiClient.get_slide_images()` 中正确使用了 `_headers(token)`。

Android 环境下 `requests` 库的 cookie-based 认证处理与 Windows 可能不同，导致仅依赖 session cookies 的 API 调用在 Android 上失败。

### 修复方案

在 `_fetch_slide_urls()` 中添加从 session cookies 中提取 token 的逻辑：

```python
# 修复后
from ktp_core.client import BASE, _headers, _json_dumps

ts = int(time.time() * 1000)
payload = {"interactid": interactid, "reqtimestamp": ts}
fallback_s = self._slide_fallback_cookies
# 从 session cookies 中提取 token（与 get_slide_images 行为一致）
token = ""
if fallback_s is not None:
    for cookie in fallback_s.cookies:
        if cookie.name == "token":
            token = cookie.value
            break
r = fallback_s.post(
    f"{BASE}//PrestudyTaskApi/preStudyList",
    headers=_headers(token),  # ← 正确携带 token
    data=_json_dumps(payload),
    timeout=60,
)
```

**修改文件**：`src/ktp_core/download_manager.py` 第 232-244 行

---

### Bug 2：APK 启动闪退（SSL 证书缺失）

**现象**：安装后点击图标→显示 Loading→立刻闪退，无任何错误日志。

**根因**：Android 系统没有系统级 CA 证书文件，`requests` 找不到证书导致 SSL 连接崩溃。

**修复方案**（不往 spec 里加 `certifi` 的解法——加 `certifi` 会破坏 p4a recipe 匹配）：

1. 从 `https://curl.se/ca/cacert.pem` 下载 CA 证书包放入项目根目录
2. `main.py` 在文件头部设置 `SSL_CERT_FILE`：

```python
# ── Android SSL 证书修复 ──
_CACERT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cacert.pem")
if os.path.exists(_CACERT_PATH):
    os.environ["SSL_CERT_FILE"] = _CACERT_PATH
```

**修改文件**：`cacert.pem`（新增）、`main.py`

---

### Bug 3：`buildozer.spec` 的版本号必须精确匹配 recipe

**现象**：`kivy>=2.3.0` 和 `Pillow>=10.0.0` 导致 p4a 不使用 recipe 而从 pip 安装 x86_64 版本，APK 运行闪退。

**原因**：p4a 的 recipe 匹配是精确版本比较。Kivy recipe 版本是 `2.3.0`，Pillow recipe 版本是 `8.4.0`。用 `>=` 会让 p4a 把 kivy/Pillow 当成"纯 pip 模块"而非 recipe，从 PyPI 下载 x86_64 wheel 打包进 APK，在手机上无法运行。

**解决**：使用精确版本号：

```ini
requirements = python3,kivy==2.3.0,requests>=2.28.0,Pillow==8.4.0
```

---

### Bug 4：pip 安装 x86_64 PIL 覆盖 recipe ARM64 版本

**现象**：APK 中可以进入 App、登录、浏览，但下载互动课件时报错：
```
ImportError: _imaging.so is for EM_X86_64 instead of EM_AARCH64
```

**原因**：Bug 3 的版本号问题导致 Pillow 通过 pip 安装 x86_64 wheel 到 `_python_bundle/site-packages/PIL/` 中，覆盖了 Android 打包流程中原应存在的 ARM64 版本。

**解决**：唯一解决方案是修复 `buildozer.spec` 的版本号并完全重新打包。直接推送 `.py` 文件无法修复，因为损坏在编译好的 `.so` 文件层。

---

### Bug 5：清除缓存后 SDL2_image 2.8.0 子模块克隆失败

**现象**：执行清理重编后构建失败，报 `fatal: unable to access 'https://skia.googlesource.com/skcms/'`

**原因**：SD2_image 2.8.0 的 libjxl→skcms 子模块在 Google 服务器上，国内被墙。**只有完全清理缓存后才会暴露**（已有缓存时不会重新 clone）。

**解决**：将 p4a recipe 中 sdl2_image 临时降级为 2.6.3（无 libjxl 依赖）。此修改仅在完全清理重建时需要，正常 VPN 环境下不需要。

```bash
SDI_RECIPE=~/ktp/.buildozer/android/platform/python-for-android/pythonforandroid/recipes/sdl2_image/__init__.py
sed -i "s/version = '2\.8\.0'/version = '2.6.3'/" "$SDI_RECIPE"
```

> **注意**：每次 `git clone` p4a 后 recipe 会恢复 2.8.0。如果之前已缓存的 2.8.0 子模块完整（VPN 环境下构建成功），则不需要此修改。

**修改文件**：p4a recipe `pythonforandroid/recipes/sdl2_image/__init__.py`

---

### Bug 6：Android 上 PIL PDF 插件缺失（`KeyError: 'PDF'`）

**现象**：幻灯片图片下载成功但 PDF 合成失败，错误日志显示 PIL 缺少 `PdfImagePlugin`。

**原因**：Android 版 Pillow 编译时 PDF 编码插件 (`PdfImagePlugin.pyc`) 被裁掉，`Image.save(path, "PDF")` 抛 `KeyError`。

**解决**：在 `download_manager.py` 中实现 `_build_pdf()` 纯 Python PDF 生成器：
- PIL 仅用于解码 PNG → 原始 RGB 字节（Android 上正常）
- PDF 二进制文件通过 `struct + zlib` 直接构建（Catalog、Pages、XObject Image、xref table）
- 不依赖 PIL 的任何编码功能

---

### Bug 7：HTML 响应误下载为文件（互动课件直链实际返回 HTML 页面）

**现象**：某些互动课件 `downloadv5.ketangpai.com` 返回 HTTP 200 + `Content-Type: text/html`，被当作真实文件下载保存，不触发 PDF 回退。

**原因**：`_download_one` 只检查 HTTP 状态码，未检查 `Content-Type`。互动课件的"直链"实际是播放页面。

**解决**：

```python
ct = r.headers.get("Content-Type", "").lower()
cd = r.headers.get("Content-Disposition", "")
if "text/html" in ct and not cd:
    raise IOError("HTML页面→回退PDF")
```

有 `Content-Disposition` 时不回退（说明确实是个文件下载），无 CD 且 Content-Type 是 HTML 时才回退。

---

## 10. 构建产物

构建成功后，APK 位于：

```
~/ktp/bin/ktp_downloader-0.1.0-arm64-v8a-debug.apk
```

文件大小约 **20 MB**，支持 arm64-v8a 架构（2020 年后几乎所有 Android 手机）。

**安装到手机**：

```bash
adb install ktp_downloader-0.1.0-arm64-v8a-debug.apk
```

---

## 快速参考卡片

```bash
# ===== 新环境一次配置 =====
sudo apt install -y openjdk-17-jdk python3-dev python3-pip python3-venv \
  autoconf automake libtool libltdl-dev unzip zip git build-essential
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
echo 'export PIP_BREAK_SYSTEM_PACKAGES=1' >> ~/.bashrc
source ~/.bashrc
pip install buildozer Cython

# ===== 每次打包 =====
cd ~/ktp
PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug

# ===== 完全清理重建（修复异常闪退时使用）=====
rm -rf ~/ktp/.buildozer/android/platform/build-arm64-v8a
cd ~/ktp && PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug

# ===== 调试：不重打包，直接推送代码到已安装 App =====
adb shell run-as com.ktp.ktp_downloader sh -c "cat > files/app/src/ktp_core/download_manager.py" < download_manager.py
adb shell run-as com.ktp.ktp_downloader rm -rf files/app/src/ktp_core/__pycache__
adb shell am force-stop com.ktp.ktp_downloader

# ===== 查看 APK 产物 =====
ls -lh ~/ktp/bin/
```

---

> **文档维护**：如果在后续打包中遇到新问题，请追加到第 6 节。
