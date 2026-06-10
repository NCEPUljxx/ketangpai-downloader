# 课堂派资料下载器

从课堂派（ketangpai.com）下载课程资料和互动课件，供个人学习备份使用。提供 Windows 桌面 GUI。

## 功能

- **三种登录方式**：账号密码 / 手机验证码（算式验证码+短信） / 微信扫码
- 课程按学年学期分组展示，支持搜索
- 资料区文件下载（目录树浏览、递归子文件夹）
- 互动课件下载：优先原始文件；禁止下载时自动获取幻灯片图片合成 PDF
- 多线程并发下载（6 线程），实时进度条
- 默认下载到桌面

## 快速开始

```powershell
cd 课堂派
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m ktp_app.main
```

或直接运行打包好的 `dist/ktp-downloader.exe`（70 MB，无需安装 Python）。

## 依赖

- Python >= 3.10
- flet, requests, pycryptodome, Pillow（详见 [requirements.txt](requirements.txt)）

## 测试

```powershell
pytest tests -q
```

## 免责声明

本工具仅供个人学习备份使用。请勿上传、分享、传播课件内容。
