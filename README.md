# 课堂派下载工具

课堂派教学平台资料/课件下载工具，支持 Windows 桌面版（Flet）和 Android APK 版（Kivy）。

## 功能

- **三种登录方式**：账号密码、短信验证码、微信扫码
- **课程浏览**：按学年/学期三级折叠，支持搜索
- **资料/课件下载**：多选批量下载，进度实时显示
- **文件夹递归**：自动遍历子目录收集所有文件
- **互动课件 PDF**：无法直链下载的互动课件自动下载幻灯片并合成为 PDF
- **文件自动命名**：Content-Disposition 解析 + Content-Type 扩展名推断

## 项目结构

```
课堂派下载工具/
├── README.md                         ← 本文档
├── Windows版/                         ← Flet 桌面客户端
│   ├── main.py                       ← 入口
│   ├── pyproject.toml                ← 项目配置
│   ├── requirements.txt              ← Python 依赖
│   ├── src/ktp_app/                  ← Flet GUI
│   ├── src/ktp_core/                 ← 核心库（pycryptodome 加密）
│   └── tests/
└── Android版/                         ← Kivy APK 客户端
    ├── main.py                        ← Kivy 主程序
    ├── buildozer.spec                 ← Android 构建配置
    ├── requirements.txt               ← Python 依赖
    ├── cacert.pem                     ← CA 证书（Android SSL）
    ├── DroidSansFallback.ttf          ← 中文字体
    ├── bin/ktp_downloader.apk         ← 原始 APK 备份
    ├── README.md                      ← Android 详细说明
    ├── WSL打包环境配置指南.md          ← 打包全流程文档
    └── src/ktp_core/                  ← 核心库（_pyaes 加密）
```

## 两端差异

| 方面 | Windows | Android |
|------|---------|---------|
| GUI 框架 | Flet (Material 3) | Kivy |
| 打包方式 | PyInstaller | Buildozer |
| 加密库 | pycryptodome (C 扩展) | _pyaes (纯 Python vendored) |
| PDF 生成 | PIL 直接写入 | 纯 Python 构建（PIL 仅解码 PNG） |
| SSL 证书 | 系统自带 | 捆绑 cacert.pem |
| 最大并发下载 | 6 线程 | 3 线程 |

## 下载安装

### Android
从 [Releases](https://github.com/NCEPUljxx/ketangpai-downloader/releases) 下载最新 APK，允许未知来源安装。

### Windows
从 [Releases](https://github.com/NCEPUljxx/ketangpai-downloader/releases) 下载 EXE，直接运行。

## 构建方法

### Android (WSL2)
详见 `Android版/WSL打包环境配置指南.md`。

```bash
cd ~/ktp
PIP_BREAK_SYSTEM_PACKAGES=1 buildozer android debug
```

### Windows
```bash
pip install -r requirements.txt
flet pack main.py
```

## License

个人学习备份用途。课堂派 API 版权归课堂派所有。
