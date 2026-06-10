"""课堂派 OpenAPI 客户端与下载逻辑。"""

from ktp_core.client import KetangpaiClient, ContentArea, FolderItem, FileItem
from ktp_core.url_resolve import resolve_direct_url, safe_filename

__all__ = [
    "KetangpaiClient",
    "ContentArea",
    "FolderItem",
    "FileItem",
    "resolve_direct_url",
    "safe_filename",
]
