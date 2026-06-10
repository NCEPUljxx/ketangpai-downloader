from __future__ import annotations

import io
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable
from urllib.parse import unquote

import requests

from ktp_core.constants import UA
from ktp_core.client import DownloadTask
from ktp_core.url_resolve import resolve_direct_url, safe_filename, target_paths

ProgressCb = Callable[[str, float, int, int, str | None], None]
"""progress_key (e.g. rel_path), fraction 0-1, current bytes, total or 0, status ok|error"""


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
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "text/plain": ".txt",
}


def _resolve_download_filename(response: requests.Response, fallback_path: str) -> str:
    """从 HTTP 响应头提取真实文件名。

    优先级：Content-Disposition > API 标题 > Content-Type 推断扩展名
    """
    # 1. Content-Disposition（服务器返回的真实文件名）
    cd = response.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\s]+)', cd, re.IGNORECASE)
    if m:
        raw = unquote(m.group(1).strip())
        if raw:
            name, _ = os.path.splitext(raw)
            ext = os.path.splitext(raw)[1]
            return name + ext if ext else raw

    # 2. 使用 API 标题作为基础名
    fname = os.path.basename(fallback_path)
    name, ext = os.path.splitext(fname)

    # 3. 如果没有扩展名，从 Content-Type 推断
    if not ext:
        ct = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        ext = _CONTENT_TYPE_EXT.get(ct, "")
        fname = name + ext

    return fname


def _download_one(
    session: requests.Session,
    root: str,
    task: DownloadTask,
    progress: ProgressCb | None,
    retries: int,
) -> DownloadResult:
    url = resolve_direct_url(task.raw_url)
    if not url:
        return DownloadResult(task.display_name, False, error="无有效下载链接")
    prog_key = task.rel_path
    d, _fp = target_paths(root, task.rel_path)
    dpart = d
    os.makedirs(dpart, exist_ok=True)
    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            with session.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                # 从服务器响应头确定真实文件名（含扩展名）
                fname = _resolve_download_filename(r, task.rel_path)
                sf = safe_filename(fname)
                fp = os.path.join(dpart, sf)
                part_path = fp + ".part"
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                with open(part_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        f.write(chunk)
                        got += len(chunk)
                        if progress and total > 0:
                            progress(prog_key, got / total, got, total, None)
                        elif progress:
                            progress(prog_key, 0.0, got, 0, None)
                if total > 0 and got < total:
                    raise IOError(f"Incomplete download: {got}/{total} bytes")
            os.replace(part_path, fp)
            if progress:
                progress(prog_key, 1.0, got, got if total == 0 else total, "ok")
            return DownloadResult(task.display_name, True, path=fp)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            try:
                if os.path.isfile(part_path):
                    os.remove(part_path)
            except OSError:
                pass
            if attempt < retries:
                import time
                time.sleep(1 + attempt)
    if progress:
        progress(prog_key, 0.0, 0, 0, last_err or "下载失败")
    return DownloadResult(task.display_name, False, error=last_err)


def _download_slides_as_pdf(
    session: requests.Session,
    slide_urls: list[str],
    dest_path: str,
    prog_key: str,
    progress: ProgressCb | None,
) -> DownloadResult:
    """下载所有幻灯片图片并合成为 PDF。"""
    from PIL import Image

    images: list[bytes] = []
    total = len(slide_urls)
    for i, url in enumerate(slide_urls):
        try:
            r = session.get(url, timeout=120)
            r.raise_for_status()
            images.append(r.content)
            if progress:
                progress(prog_key, (i + 1) / total, i + 1, total, None)
        except Exception as e:
            if progress:
                progress(prog_key, 0.0, 0, 0, f"幻灯片 {i+1} 下载失败: {e}")
            return DownloadResult(
                os.path.basename(dest_path), False,
                error=f"幻灯片 {i+1} 下载失败: {e}",
            )
    try:
        pil_images = []
        for data in images:
            img = Image.open(io.BytesIO(data))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            pil_images.append(img)
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        pil_images[0].save(
            dest_path, "PDF", save_all=True, append_images=pil_images[1:],
        )
        if progress:
            progress(prog_key, 1.0, total, total, "ok")
        return DownloadResult(os.path.basename(dest_path), True, path=dest_path)
    except Exception as e:
        if progress:
            progress(prog_key, 0.0, 0, 0, str(e))
        return DownloadResult(
            os.path.basename(dest_path), False, error=f"PDF 合成失败: {e}",
        )


def _download_task_worker(
    dm: DownloadManager,
    dest_root: str,
    task: DownloadTask,
    progress: ProgressCb | None,
) -> DownloadResult:
    """Run inside worker thread so ``threading.local()`` session is bound to that thread."""
    session = dm._session()
    result = _download_one(session, dest_root, task, progress, dm.retries)
    if result.ok:
        return result
    # Fallback: try slide images → PDF for courseware
    if dm._slide_fallback_cookies is not None and task.file_id:
        slide_urls = dm._fetch_slide_urls(task.file_id)
        if slide_urls:
            d, fp = target_paths(dest_root, task.rel_path)
            sf = safe_filename(os.path.basename(fp))
            pdf_path = os.path.join(d, os.path.splitext(sf)[0] + ".pdf")
            os.makedirs(d, exist_ok=True)
            return _download_slides_as_pdf(
                session, slide_urls, pdf_path, task.rel_path, progress,
            )
    return result


class DownloadManager:
    def __init__(
        self,
        max_workers: int = 6,
        retries: int = 2,
        copy_cookies_from: requests.Session | None = None,
        slide_fallback_cookies: requests.Session | None = None,
    ) -> None:
        self.max_workers = max_workers
        self.retries = retries
        self._copy_cookies_from = copy_cookies_from
        self._slide_fallback_cookies = slide_fallback_cookies
        self._session_local = threading.local()
        self._slide_urls_lock = threading.Lock()
        self._slide_urls_cache: dict[str, list[str] | None] = {}

    def _fetch_slide_urls(self, interactid: str) -> list[str] | None:
        with self._slide_urls_lock:
            cached = self._slide_urls_cache.get(interactid)
            if cached is not None:
                return cached
        try:
            from ktp_core.client import BASE, _headers, _json_dumps

            ts = int(time.time() * 1000)
            payload = {"interactid": interactid, "reqtimestamp": ts}
            fallback_s = self._slide_fallback_cookies
            r = fallback_s.post(
                f"{BASE}//PrestudyTaskApi/preStudyList",
                headers=_headers(""),
                data=_json_dumps(payload),
                timeout=60,
            )
            r.raise_for_status()
            body = r.json()
            if body.get("code") != 10000:
                with self._slide_urls_lock:
                    self._slide_urls_cache[interactid] = None
                return None
            pages = body.get("data", {}).get("data") or {}
            urls: list[str] = []
            for k in sorted(pages.keys(), key=int):
                src = pages[k].get("src", "")
                if src:
                    url = "https:" + src if src.startswith("//") else src
                    urls.append(url)
            result = urls or None
            with self._slide_urls_lock:
                self._slide_urls_cache[interactid] = result
            return result
        except Exception:
            with self._slide_urls_lock:
                self._slide_urls_cache[interactid] = None
            return None

    def _session(self) -> requests.Session:
        s = getattr(self._session_local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Referer": "https://www.ketangpai.com/",
                    "Origin": "https://www.ketangpai.com",
                }
            )
            if self._copy_cookies_from is not None:
                try:
                    s.cookies.update(self._copy_cookies_from.cookies)
                    # Also pass token as header for downloadv5.ketangpai.com
                    for cookie in self._copy_cookies_from.cookies:
                        if cookie.name == "token":
                            s.headers["token"] = cookie.value
                            break
                except Exception:  # noqa: BLE001
                    pass
            self._session_local.session = s
        return s

    def run_tasks(
        self,
        tasks: list[DownloadTask],
        dest_root: str,
        progress: ProgressCb | None = None,
    ) -> list[DownloadResult]:
        results: list[DownloadResult] = []
        if not tasks:
            return results
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = [ex.submit(_download_task_worker, self, dest_root, t, progress) for t in tasks]
            for fut in as_completed(futs):
                results.append(fut.result())
        return results
