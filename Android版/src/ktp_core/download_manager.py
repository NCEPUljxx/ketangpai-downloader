from __future__ import annotations

import io
import os
import re
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable
from urllib.parse import unquote

import requests

from ktp_core.constants import UA
from ktp_core.client import DownloadTask
from ktp_core.url_resolve import resolve_direct_url, safe_filename, target_paths

ProgressCb = Callable[[str, float, int, int, str | None], None]


@dataclass
class DownloadResult:
    name: str
    ok: bool
    path: str | None = None
    error: str | None = None


_CONTENT_TYPE_EXT = {
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/pdf": ".pdf",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/x-7z-compressed": ".7z",
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "video/mp4": ".mp4", "audio/mpeg": ".mp3",
    "text/plain": ".txt",
}


def _resolve_download_filename(response, fallback_path):
    cd = response.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\s]+)', cd, re.IGNORECASE)
    if m:
        raw = unquote(m.group(1).strip())
        if raw:
            name, ext = os.path.splitext(raw)
            return name + ext if ext else raw
    fname = os.path.basename(fallback_path)
    name, ext = os.path.splitext(fname)
    if not ext:
        ct = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        ext = _CONTENT_TYPE_EXT.get(ct, "")
        fname = name + ext
    return fname


def _download_one(session, root, task, progress, retries):
    url = resolve_direct_url(task.raw_url)
    prog_key = task.rel_path
    if not url:
        if progress: progress(prog_key, 0.0, 0, 0, "无链接")
        return DownloadResult(task.display_name, False, error="无链接")

    d, _ = target_paths(root, task.rel_path)
    os.makedirs(d, exist_ok=True)
    pp = ""
    last_err = None
    for attempt in range(retries + 1):
        try:
            with session.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                ct = r.headers.get("Content-Type", "").lower()
                cd = r.headers.get("Content-Disposition", "")
                if "text/html" in ct and not cd:
                    raise IOError("HTML→回退PDF")

                fname = _resolve_download_filename(r, task.rel_path)
                fp = os.path.join(d, safe_filename(fname))
                pp = fp + ".part"
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                with open(pp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        if not chunk: continue
                        f.write(chunk); got += len(chunk)
                        if progress and total > 0:
                            progress(prog_key, got / total, got, total, None)
                        elif progress:
                            progress(prog_key, 0.0, got, 0, None)
                if total > 0 and got < total:
                    raise IOError("未完整: %d/%d" % (got, total))
            os.replace(pp, fp)
            if progress: progress(prog_key, 1.0, got, total, "ok")
            return DownloadResult(task.display_name, True, path=fp)
        except Exception as e:
            last_err = str(e)
            if pp and os.path.isfile(pp):
                try: os.remove(pp)
                except OSError: pass
            if attempt < retries: time.sleep(1 + attempt)
    if progress: progress(prog_key, 0.0, 0, 0, last_err or "下载失败")
    return DownloadResult(task.display_name, False, error=last_err)


# ═══════ 自包含 PDF 生成器（不依赖 PIL PDF 写入、不在模块级 import PIL）═══════


def _png_to_raw_rgb(blob):
    """解码 PNG → (width, height, raw RGB bytes)。"""
    # PIL 只在函数内导入（Android worker 线程可用）
    from PIL import Image
    img = Image.open(io.BytesIO(blob))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img.width, img.height, img.tobytes("raw", "RGB")


def _build_pdf(write_path, pages):
    """pages = [(width, height, raw_rgb_bytes), ...] → 写入 PDF"""
    PW, PH = 595, 842
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    pos = []

    def obj(b):
        pos.append(buf.tell())
        buf.write(b)

    obj(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    kids = " ".join("%d 0 R" % (i * 3 + 4) for i in range(len(pages)))
    obj(("2 0 obj\n<< /Type /Pages /Kids [%s] /Count %d >>\nendobj\n" % (kids, len(pages))).encode())

    for idx, (w, h, raw) in enumerate(pages):
        s = min(PW / w, PH / h) if w and h else 1.0
        iw, ih = w * s, h * s
        x, y = (PW - iw) / 2, (PH - ih) / 2
        cmp = zlib.compress(raw)
        n = idx * 3 + 3
        obj(("%d 0 obj\n<< /Type /XObject /Subtype /Image\n"
             "   /Width %d /Height %d /ColorSpace /DeviceRGB\n"
             "   /BitsPerComponent 8 /Filter /FlateDecode\n"
             "   /Length %d >>\nstream\n" % (n, w, h, len(cmp))).encode())
        buf.write(cmp)
        buf.write(b"\nendstream\nendobj\n")
        cn = n + 1
        st = "q %.1f 0 0 %.1f %.1f %.1f cm /I%d Do Q\n" % (iw, ih, x, y, idx)
        obj(("%d 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n" % (cn, len(st), st)).encode())
        pn = n + 2
        obj(("%d 0 obj\n<< /Type /Page /Parent 2 0 R\n"
             "   /MediaBox [0 0 %d %d]\n"
             "   /Contents %d 0 R\n"
             "   /Resources << /XObject << /I%d %d 0 R >> >> >>\nendobj\n" % (pn, PW, PH, cn, idx, n)).encode())

    xr = buf.tell()
    ntot = len(pos) + 1
    buf.write(("xref\n0 %d\n0000000000 65535 f \n" % ntot).encode())
    for p in pos:
        buf.write(("%010d 00000 n \n" % p).encode())
    buf.write(("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (ntot, xr)).encode())

    os.makedirs(os.path.dirname(write_path) or ".", exist_ok=True)
    with open(write_path, "wb") as f:
        f.write(buf.getvalue())


def _slides_to_pdf(images, dest_path, prog_key, total, progress):
    """下载好的 PNG 字节列表 → PDF 文件。返回 (ok, error_str)。"""
    try:
        pages = []
        for i, d in enumerate(images):
            w, h, raw = _png_to_raw_rgb(d)
            pages.append((w, h, raw))
        _build_pdf(dest_path, pages)
        if progress: progress(prog_key, 1.0, total, total, "ok")
        return True, None
    except Exception as e:
        # 诊断：将第一张图片保存到磁盘，检查是否是有效 PNG
        import traceback
        try:
            dbg = os.path.join(os.path.dirname(dest_path), "__slide_dbg")
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, "first.png"), "wb") as f:
                f.write(images[0])
            with open(os.path.join(dbg, "error.txt"), "w") as f:
                f.write(f"images count: {len(images)}\n")
                f.write(f"first size: {len(images[0])}\n")
                f.write(f"first hex: {images[0][:20].hex()}\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        if progress: progress(prog_key, 0.0, 0, 0, str(e)[:80])
        return False, str(e)


def _download_slides_as_pdf(session, slide_urls, dest_path, prog_key, progress):
    total = len(slide_urls)
    images = []
    for i, url in enumerate(slide_urls):
        try:
            r = session.get(url, timeout=120)
            r.raise_for_status()
            images.append(r.content)
            if progress:
                progress(prog_key, (i + 1) / total, i + 1, total, None)
        except Exception as e:
            if progress:
                progress(prog_key, 0.0, 0, 0, f"幻灯片{i+1}失败")
            return DownloadResult(os.path.basename(dest_path), False, error=f"幻灯片{i+1}下载失败: {e}")

    ok, err = _slides_to_pdf(images, dest_path, prog_key, total, progress)
    if ok:
        return DownloadResult(os.path.basename(dest_path), True, path=dest_path)
    return DownloadResult(os.path.basename(dest_path), False, error=f"PDF生成失败: {err}")


# ═══════ Worker / Manager ═══════


def _download_task_worker(dm, dest_root, task, progress):
    session = dm._session()
    result = _download_one(session, dest_root, task, progress, dm.retries)
    if result.ok:
        return result
    # Fallback: slide images → PDF
    has_cookies = dm._slide_fallback_cookies is not None
    has_fid = bool(task.file_id)
    if not (has_cookies and has_fid):
        return result
    urls = dm._fetch_slide_urls(task.file_id)
    if not urls:
        if progress: progress(task.rel_path, 0.0, 0, 0, "无幻灯片数据")
        return DownloadResult(task.display_name, False, error="无幻灯片数据")
    d, fp = target_paths(dest_root, task.rel_path)
    pdf = os.path.join(d, os.path.splitext(safe_filename(os.path.basename(fp)))[0] + ".pdf")
    os.makedirs(d, exist_ok=True)
    return _download_slides_as_pdf(session, urls, pdf, task.rel_path, progress)


class DownloadManager:
    def __init__(self, max_workers=6, retries=2, copy_cookies_from=None,
                 slide_fallback_cookies=None, auth_token=None):
        self.max_workers = max_workers
        self.retries = retries
        self._copy_cookies_from = copy_cookies_from
        self._slide_fallback_cookies = slide_fallback_cookies
        self._auth_token = auth_token or ""
        self._session_local = threading.local()
        self._slide_urls_lock = threading.Lock()
        self._slide_urls_cache: dict[str, list[str] | None] = {}

    def _fetch_slide_urls(self, interactid):
        with self._slide_urls_lock:
            if interactid in self._slide_urls_cache:
                return self._slide_urls_cache[interactid]
        def _dbg(msg):
            try:
                with open(os.path.join(os.path.dirname(__file__), "_prestudy.log"), "a") as f:
                    f.write("%s [%s] %s\n" % (time.strftime("%H:%M:%S"), interactid[:12], msg))
            except Exception:
                pass
        _dbg("start")
        try:
            token = self._auth_token
            _dbg("token_from_auth=%s" % token[:10] if token else "token_from_auth=empty")
            if not token and self._slide_fallback_cookies:
                for c in self._slide_fallback_cookies.cookies:
                    if c.name == "token": token = c.value or ""; break
            if not token:
                token = self._session().headers.get("token", "") or ""
            _dbg("token_final=%s" % token[:10] if token else "token_final=empty")

            import json as _json
            payload = _json.dumps(
                {"interactid": interactid, "reqtimestamp": int(time.time() * 1000)},
                separators=(",", ":"))
            r = self._slide_fallback_cookies.post(
                "https://openapiv5.ketangpai.com/PrestudyTaskApi/preStudyList",
                headers={"Accept": "application/json", "Content-Type": "application/json",
                         "token": token, "User-Agent": UA,
                         "Origin": "https://www.ketangpai.com", "Referer": "https://www.ketangpai.com/"},
                data=payload, timeout=60)
            _dbg("http=%d" % r.status_code)
            r.raise_for_status()
            body = r.json()
            _dbg("code=%s pages=%d" % (body.get("code"), len(body.get("data",{}).get("data",{}) or {})))
            if body.get("code") != 10000:
                with self._slide_urls_lock: self._slide_urls_cache[interactid] = None
                _dbg("FAIL: bad code")
                return None
            pages = body.get("data", {}).get("data") or {}
            urls = []
            for k in sorted(pages.keys(), key=int):
                src = pages[k].get("src", "")
                if src:
                    urls.append("https:" + src if src.startswith("//") else src)
            result = urls or None
            with self._slide_urls_lock: self._slide_urls_cache[interactid] = result
            _dbg("OK: %d urls" % len(result or []))
            return result
        except Exception as e:
            _dbg("EXCEPT: %s" % str(e)[:120])
            with self._slide_urls_lock: self._slide_urls_cache[interactid] = None
            return None

    def _session(self):
        s = getattr(self._session_local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": UA, "Accept": "*/*",
                              "Referer": "https://www.ketangpai.com/",
                              "Origin": "https://www.ketangpai.com"})
            if self._copy_cookies_from:
                try:
                    s.cookies.update(self._copy_cookies_from.cookies)
                    for c in self._copy_cookies_from.cookies:
                        if c.name == "token": s.headers["token"] = c.value; break
                except Exception: pass
            self._session_local.session = s
        return s

    def run_tasks(self, tasks, dest_root, progress=None):
        results = []
        if not tasks: return results
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = [ex.submit(_download_task_worker, self, dest_root, t, progress) for t in tasks]
            for fut in as_completed(futs):
                results.append(fut.result())
        return results
