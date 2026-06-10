# Research Notes: 课堂派下载器

## 项目位置
- Windows: `e:/华电/资料/课堂派资料下载/新建文件夹/Windows版完整项目文件/`
- Android: `e:/华电/资料/课堂派资料下载/新建文件夹/课堂派Android版完整项目/`

## 项目结构与原理

### 相同点
- 核心库 `ktp_core` API 端点相同：`https://openapiv5.ketangpai.com`
- 下载流程：直接下载 → 失败时回退到幻灯片→PDF

### 不同点
| 方面 | Windows | Android |
|------|---------|---------|
| GUI 框架 | Flet (Material 3) | Kivy |
| 打包方式 | PyInstaller (flet pack) | Buildozer |
| 加密库 | pycryptodome (C扩展) | vendored _pyaes (纯Python) |
| 中文字体 | 系统字体 | 捆绑 DroidSansFallback.ttf |
| 最大并发 | 6 线程 | 3 线程 |
| download_manager.py | 原始版本（两端不同步） | 已独立修复（含token+HTML检测+纯Python PDF） |

## Android 端已修复的 Bug（最终状态）

### Bug 1：互动课件 token 为空
`_fetch_slide_urls` 传入空 token → preStudyList API 认证失败。已从 `auth_token`/cookies/session headers 中提取并正确传入。

### Bug 2：HTML 误下载为文件
互动课件 `downloadv5.ketangpai.com` 返回 `text/html` + HTTP 200，被当作文件保存。增加 `Content-Type` 检测（HTML + 无 Content-Disposition → 回退PDF）。

### Bug 3：Android PIL PDF 插件缺失
Pillow 的 `PdfImagePlugin` 在 Android 编译时被裁掉。实现 `_build_pdf()` 纯 Python PDF 生成器（struct + zlib），PIL 仅解码 PNG → RGB。

### Bug 4：buildozer.spec 版本号错配
`kivy>=2.3.0`/`Pillow>=10.0.0` → pip 安装 x86_64 版。改为 `kivy==2.3.0`/`Pillow==8.4.0` 精确匹配 p4a recipe。

### Bug 5：Android SSL 证书缺失
捆绑 `cacert.pem` + `main.py` 头部设置 `SSL_CERT_FILE`（不往 spec 加 certifi 破坏 recipe 匹配）。

### Bug 6：build 目录损坏
`.buildozer/.../build` 的 inode 变成 2，pip 安装到根目录。删除整个 `build-arm64-v8a` 重建解决。

### Bug 7：SDL2_image 2.8.0 skcms 子模块被墙
libjxl→skcms 在 Google 服务器 → 国内 VPN 不够稳时克隆失败。已解决（VPN 正常时自动通过，临时可降级 recipe）。

## API 验证结果（2026-06-10）

茶文化概论 7 个课件：
- 2 个是普通 powerpoint 文件（Content-Type: application/octet-stream + Content-Disposition）→ 直链下载成功
- 5 个是互动课件（Content-Type: application/octet-stream + Content-Disposition）→ 直链也能下，但 preStudyList 有幻灯片数据可备选

所有 7 个 `preStudyList` 调用均返回 10000 + 39~59 页幻灯片，图片可下载。
