from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, unquote, urlparse

DOCUMENT_HOST = "document.ketangpai.com"
INTERNAL_MARKERS = ("-internal",)


def resolve_direct_url(raw: str | None) -> str | None:
    """将课堂返回的附件 URL 转为可直连下载的地址。"""
    if not raw:
        return None
    u = raw.strip()
    if not u:
        return None
    try:
        parsed = urlparse(u)
        if parsed.netloc.lower().endswith(DOCUMENT_HOST) and parsed.query:
            qs = parse_qs(parsed.query)
            furls = qs.get("furl") or qs.get("FURL")
            if furls:
                inner = unquote(furls[0])
                u = inner.strip()
        for marker in INTERNAL_MARKERS:
            u = u.replace(marker, "")
        return u.strip()
    except Exception:
        return raw


_INVALID_WIN = re.compile(r'[\\/:*?"<>|]')


def safe_filename(name: str, max_len: int = 180) -> str:
    base = _INVALID_WIN.sub("_", name).strip()
    if not base:
        base = "download"
    root, ext = os.path.splitext(base)
    if len(base) > max_len:
        keep = max_len - len(ext)
        if keep < 1:
            keep = max_len
        base = root[:keep] + ext
    return base


def target_paths(root: str, rel_path: str) -> tuple[str, str]:
    """返回 (目录路径, 完整文件路径)。"""
    rel = rel_path.replace("\\", "/")
    parent, fname = os.path.split(rel)
    d = os.path.join(root, *parent.split("/")) if parent else root
    fp = os.path.join(d, fname)
    return d, fp
