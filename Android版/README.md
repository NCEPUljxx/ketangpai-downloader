# 课堂派资料下载 (Kivy Android 版)

课堂派教学平台资料/课件下载工具，支持 Android APK 安装。

## 功能

- **三种登录方式**：账号密码、短信验证码、微信扫码
- **课程浏览**：按学年/学期三级折叠，支持搜索
- **资料/课件下载**：多选批量下载，进度实时显示
- **文件夹递归**：自动遍历子目录收集所有文件
- **互动课件回退**：无法直链下载的互动课件自动下载幻灯片并合成为 PDF
- **文件自动命名**：支持 Content-Disposition 解析和 Content-Type 扩展名推断

## 项目结构

```
├── main.py                       # Kivy 主程序（UI + 业务逻辑）
├── buildozer.spec                # Buildozer Android 构建配置
├── requirements.txt              # Python 依赖
├── DroidSansFallback.ttf         # 中文字体（CJK 全覆盖）
├── cacert.pem                    # CA 证书包（Android SSL 修复）
├── bin/
│   └── ktp_downloader.apk        # 原始可运行 APK
├── src/
│   └── ktp_core/                 # 后端共享代码
│       ├── __init__.py
│       ├── client.py             # 课堂派 API 客户端
│       ├── download_manager.py   # 多线程下载管理器（★含互动课件PDF回退）
│       ├── encrypt.py            # AES 密码加密
│       ├── url_resolve.py        # URL 解析
│       ├── constants.py          # 常量
│       └── _pyaes/               # 纯 Python AES 实现（vendored）
└── WSL打包环境配置指南.md         # WSL 打包全流程文档
```

## 系统要求

- **构建环境**：Windows 10/11 + WSL2 (Ubuntu 24.04)
- **Android**：Android 8.0+ (API 26+), arm64-v8a
- **磁盘空间**：至少 10GB（构建缓存约 3GB）

## 构建方法

详细配置和问题排查见 [WSL打包环境配置指南.md](./WSL打包环境配置指南.md)。

### 快速命令

```bash
# WSL 中
cd ~/ktp
PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug
```

APK 输出：`~/ktp/bin/ktp_downloader-0.1.0-arm64-v8a-debug.apk`

## 调试方法（无需重新打包）

修改 Python 代码后，可通过 adb 直接推送到已安装的 App：

```bash
# 推送修改后的文件
adb shell run-as com.ktp.ktp_downloader sh -c "cat > files/app/src/ktp_core/download_manager.py" < download_manager.py

# 清除 Python 缓存
adb shell run-as com.ktp.ktp_downloader rm -rf files/app/src/ktp_core/__pycache__

# 强制重启 App
adb shell am force-stop com.ktp.ktp_downloader
```

## 代码修改记录

1. **UI 框架**：从 Flet 改为 Kivy（Flet 在国产手机上无法运行）
2. **加密**：`pycryptodome` → vendored `_pyaes`（纯 Python，避免 C 扩展兼容性问题）
3. **中文字体**：捆绑 DroidSansFallback，覆盖全部 CJK + 拉丁字符
4. **目录选择**：移除 tkinter 依赖（Android 不支持），改为文本输入路径
5. **课程列表**：三级折叠（学年 → 学期 → 课程），折叠学年时联动隐藏已展开的子课程
6. **折叠图标**：`▸/▾` → `+/-`（Android 字体不支持 Unicode 三角符号，显示为乱码）
7. **进度弹窗标题**：title 设为空（避免中文字体渲染为乱码）
8. **文件浏览**：返回按钮为逐级返回上级目录（非直接退出）
9. **微信二维码**：服务端下载到本地文件再显示（Kivy Image 不支持直接加载 URL）
10. **SSL 证书**：捆绑 `cacert.pem` 到 APK，启动时设置 `SSL_CERT_FILE` 环境变量
11. **互动课件 PDF**：直链下载失败时自动调用 `preStudyList` API 获取幻灯片图片，纯 Python 生成 PDF（PIL 仅解码 PNG → RGB）
12. **文件扩展名**：从 `Content-Disposition` 或 `Content-Type` 自动推断
13. **下载完成提示**：仅在底部状态栏显示结果，不弹窗

## 许可证

个人学习备份用途。课堂派 API 版权归课堂派所有。
