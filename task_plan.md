# Task Plan: 修复Android端互动课件下载Bug

## Goal
修复Android端课堂派下载器中"互动课件无法下载、自动转为PDF"的bug。Windows端此功能正常，需要对比两端代码找出差异并修复Android端。

## Phases
- [x] Phase 1: 创建计划文件 ✓
- [x] Phase 2: 阅读并理解Windows端项目结构和核心逻辑 ✓
- [x] Phase 3: 阅读并理解Android端项目结构和核心逻辑 ✓
- [x] Phase 4: 对比两端代码，定位互动课件下载的差异 ✓
- [x] Phase 5: 修复Android端bug ✓
- [x] Phase 6: 打包Android APK 指令 ✓

## Status
**全部完成** ✅

## Key Findings
- 两端核心代码 `client.py`, `download_manager.py`, `url_resolve.py`, `constants.py` 完全一致
- GUI框架不同：Windows用Flet，Android用Kivy
- 加密方式不同：Windows用pycryptodome，Android用vendored _pyaes

### Bug根因
`DownloadManager._fetch_slide_urls()` 调用 `PrestudyTaskApi/preStudyList` API时使用了 `_headers("")`（空token），而同样的API调用在 `KetangpaiClient.get_slide_images()` 中正确使用了 `_headers(token)`。

Android环境下的 requests 库对 cookie-based 认证的处理可能与 Windows 不同，导致该 API 调用失败，幻灯片URL获取失败，PDF回退流程中断。

### 修复内容
在 `_fetch_slide_urls()` 方法中添加了从 session cookies 中提取 token 的逻辑，并将其传入 `_headers(token)`，与 `KetangpaiClient.get_slide_images()` 的行为保持一致。

**修改文件**：
- `课堂派Android版完整项目/src/ktp_core/download_manager.py` 第182-197行
- `Windows版完整项目文件/src/ktp_core/download_manager.py` 第182-197行（同步修复）

### 打包APK命令
参考下方 build_instructions.md
