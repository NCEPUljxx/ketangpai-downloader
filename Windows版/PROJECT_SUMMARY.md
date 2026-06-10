# 课堂派资料下载器 — 项目摘要

## 1. 项目概述

从课堂派（ketangpai.com）下载课程资料和互动课件，供个人学习备份。桌面 GUI 基于 Flet 0.85，`ktp_core` 为纯 Python 业务逻辑层。

## 2. 项目结构

```
├── src/
│   ├── ktp_core/              # 核心逻辑（无 UI 依赖）
│   │   ├── client.py          # API 客户端：登录、课程/文件列表、幻灯片获取
│   │   ├── encrypt.py         # AES-128-CBC 密码加密
│   │   ├── url_resolve.py     # URL 解析：代理链接转直链、安全文件名
│   │   └── download_manager.py # 多线程下载 + 幻灯片→PDF 回退
│   └── ktp_app/
│       └── main.py            # Flet GUI
├── tests/                     # 单元测试
├── dist/                      # 打包的 Windows exe
├── pyproject.toml             # 项目配置
└── requirements.txt           # 运行时依赖
```

## 3. 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python >= 3.10 |
| GUI | Flet 0.85（Material 3，Indigo 主题） |
| HTTP | requests |
| 加密 | pycryptodome（AES-CBC） |
| PDF 合成 | Pillow |
| 打包 | PyInstaller（flet pack） |
| 文件对话框 | tkinter |

## 4. 架构

```
GUI 层 (main.py) → asyncio.to_thread() → 业务层 (ktp_core)
```

`ktp_core` 为同步代码，GUI 通过线程池调用避免阻塞 UI。

## 5. 核心 API 端点

Base URL: `https://openapiv5.ketangpai.com`

| 端点 | 用途 |
|------|------|
| `/UserApi/login` | 密码登录 |
| `/UserApi/getFigureCode` | 获取算式验证码 |
| `/UserApi/sendCode` | 发送短信验证码 |
| `/UserApi/loginByMobile` | 短信登录 |
| `/wechat/login` + `/UserApi/checkWechatCode` | 微信扫码登录 |
| `/CourseApi/semesterCourseList` | 课程列表 |
| `/FutureV2/CourseMeans/getCourseContent` | 文件列表（回退：getImportList、getCourseList） |
| `/PrestudyTaskApi/preStudyList` | 获取互动课件幻灯片签名 URL（参数 `interactid`） |

所有请求携带 `token` header，登录后 token 作为 `.ketangpai.com` 域 cookie 供 `downloadv5.ketangpai.com` 验证。

## 6. 下载机制

### 资料区文件
直接使用 `downloadv5.ketangpai.com/File/download/id/...` 下载，需携带 token cookie。

### 互动课件
1. 优先直接下载（同上）
2. 失败时调用 `PrestudyTaskApi/preStudyList`（参数 `interactid` = 文件 ID）获取每页幻灯片签名 URL
3. 下载所有幻灯片 PNG，用 Pillow 合成为 PDF

## 7. 短信登录流程

1. 用户输入手机号 → 点击"发送验证码"
2. `getFigureCode` 获取算式验证码图片 → 显示给用户
3. 用户输入计算结果 → `sendCode` 发送短信
4. 用户输入 6 位短信验证码 → `loginByMobile` 完成登录

## 8. 关键设计决策

- **目录浏览是客户端侧过滤**：API 的 `dirid` 被服务端忽略，通过 `folder_id` 自行过滤
- **文件夹是虚拟的**：从文件元数据的 `folder`/`directory` 字段提取父目录 ID 并去重
- **下载线程模型**：`ThreadPoolExecutor`（6 线程），每线程 `threading.local()` 独立 Session
- **幻灯片 URL 缓存**：同文件多页共享 OSS 签名有效期，避免重复 API 调用
- **文件对话框**：使用 tkinter 原生对话框，避开 Flet FilePicker 兼容问题
- **密码加密**：AES-128-CBC / PKCS7，密钥 `ktp4567890123456`
