from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import requests

from ktp_core.constants import UA
from ktp_core.encrypt import encrypt_password

BASE = "https://openapiv5.ketangpai.com"


class FigureCodeError(Exception):
    """图形验证码错误，服务器返回了新图片。"""

    def __init__(self, new_image: bytes) -> None:
        super().__init__("图形验证码错误")
        self.new_image = new_image


class ContentArea(str, Enum):
    MATERIALS = "materials"
    COURSEWARE = "courseware"


def _headers(token: str) -> dict[str, str]:
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Origin": "https://www.ketangpai.com",
        "Referer": "https://www.ketangpai.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": UA,
        "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    if token:
        h["token"] = token
    return h


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


@dataclass
class Course:
    id: str
    name: str
    classname: str = ""
    code: str = ""
    semester: str = ""
    term: str = ""
    username: str = ""
    total: int = 0
    role: int = 0


@dataclass
class FolderItem:
    id: str
    name: str
    parent_id: str | None = None  # 父文件夹 ID，用于客户端侧目录过滤


@dataclass
class FileItem:
    id: str
    course_id: str
    name: str
    raw_url: str | None
    folder_id: str | None = None
    folder_title: str | None = None
    filesize: int = 0  # 文件大小（字节），0 表示未知
    folder_parent_id_for_nav: str | None = None  # 所在 folder 记录的父目录 id（用于列出子文件夹）


@dataclass
class DownloadTask:
    """扁平下载任务。"""

    rel_path: str
    display_name: str
    raw_url: str | None
    file_id: str
    course_id: str


def _parse_filesize(raw: Any) -> int:
    """将 API 返回的文件大小字符串（如 '44.92KB', '2.5MB'）转为字节数。"""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip().upper()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        pass
    m = re.match(r"([\d.]+)\s*(B|KB|MB|GB|TB)?", s)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2) or "B"
    multipliers = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
    return int(num * multipliers.get(unit, 1))


def _file_item_from_course_row(row: dict[str, Any], course_id: str) -> FileItem:
    att = (row.get("attachment") or [{}])[0]
    size = _parse_filesize(att.get("size", 0))
    return FileItem(
        id=str(row["id"]),
        course_id=course_id,
        name=row.get("title") or row.get("name") or "file",
        raw_url=att.get("url"),
        filesize=size,
    )


def _folder_parent_id(meta: dict[str, Any]) -> str | None:
    """从 API 返回的 folder/directory 字典取父目录 id；无则返回 None（与旧逻辑兼容）。"""
    parent = meta.get("parent")
    if isinstance(parent, dict) and parent.get("id") is not None:
        return str(parent["id"]).strip() or None
    for key in ("parent_id", "parentid", "pid", "fatherid", "dirpid", "pdirid", "parid"):
        v = meta.get(key)
        if v is None or v == "":
            continue
        s = str(v).strip()
        if s and s != "0":
            return s
    return None


def _filter_items_for_directory(items: list[FolderItem | FileItem], dir_id: str) -> list[FolderItem | FileItem]:
    if dir_id == "0":
        # 若任一文件夹带 parent_id，则根目录只显示顶级文件夹；否则保持旧行为（全部虚拟文件夹都列在根）
        any_parent_hint = any(
            isinstance(it, FolderItem) and it.parent_id not in (None, "", "0") for it in items
        )
        out_root: list[FolderItem | FileItem] = []
        for it in items:
            if isinstance(it, FolderItem):
                if not any_parent_hint or it.parent_id in (None, "", "0"):
                    out_root.append(it)
            elif it.folder_id is None:
                out_root.append(it)
        return out_root

    did = str(dir_id)
    # 当前目录下的「子文件夹」：虚拟 FolderItem 标明 parent_id，或由文件的 folder/directory 记录推断父子关系
    child_folder_ids: set[str] = {
        str(fi.folder_id)
        for fi in items
        if isinstance(fi, FileItem)
        and fi.folder_id
        and fi.folder_parent_id_for_nav == did
    }

    folder_seen: set[str] = set()
    file_out: list[FileItem] = []
    folder_out: list[FolderItem] = []

    for it in items:
        if isinstance(it, FolderItem):
            fid = str(it.id)
            in_tree = it.parent_id == did or fid in child_folder_ids
            if in_tree and fid not in folder_seen:
                folder_seen.add(fid)
                folder_out.append(it)

    for it in items:
        if isinstance(it, FileItem) and it.folder_id == did:
            file_out.append(it)

    return [*folder_out, *file_out]


class KetangpaiClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._listing_cache: dict[tuple[str, ContentArea], list[FolderItem | FileItem]] = {}

    def invalidate_course_list_cache(
        self,
        course_id: str | None = None,
        area: ContentArea | None = None,
    ) -> None:
        """Clear listing cache entries.

        No arguments: clear all cached listings.
        Only ``course_id``: remove both content areas for that course.
        Both ``course_id`` and ``area``: remove a single cache slot.
        """
        if course_id is None and area is None:
            self._listing_cache.clear()
            return
        if course_id is None:
            return
        if area is None:
            for key in list(self._listing_cache.keys()):
                if key[0] == course_id:
                    del self._listing_cache[key]
            return
        self._listing_cache.pop((course_id, area), None)

    def retain_listing_cache_for_course_only(self, course_id: str) -> None:
        """Drop listings for other courses (memory) when opening a course."""
        for key in list(self._listing_cache.keys()):
            if key[0] != course_id:
                del self._listing_cache[key]

    def login_password(self, email: str, password: str) -> str:
        ts = int(time.time() * 1000)
        url = f"{BASE}/UserApi/login"
        enc = encrypt_password(password)
        data = {
            "email": email,
            "password": enc,
            "remember": "0",
            "code": "",
            "mobile": "",
            "type": "login",
            "encryption": 1,
            "reqtimestamp": ts,
        }
        r = self.session.post(url, headers=_headers(""), data=_json_dumps(data), timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            raise RuntimeError(body.get("message") or body.get("msg") or "登录失败")
        token = body["data"]["token"]
        self.session.cookies.set("token", token, domain=".ketangpai.com", path="/")
        return token

    def get_figure_challenge(self) -> tuple[str, bytes]:
        """获取图形验证码 sessionid 与 PNG 字节。

        重要：POST getFigureCode 响应中包含 image_url，不要在服务端会话中
        额外调用 GET /verify 获取图片 —— 每次 GET 都会重置验证码挑战，
        导致用户输入的答案对不上服务端的当前挑战。
        """
        ts = int(time.time() * 1000)
        url = f"{BASE}/UserApi/getFigureCode"
        r = self.session.post(url, headers=_headers(""), data=_json_dumps({"reqtimestamp": ts}), timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            raise RuntimeError(body.get("message") or body.get("msg") or "获取验证码失败")
        sessionid = body["data"]["sessionid"]
        image_url = body["data"]["url"]
        # 通过同一个 session 获取图片（与 getFigureCode 共享 cookie）
        # 注意：这次 GET 会重置挑战，但这是必需的 —— 服务端通过这次 GET
        # 确认客户端已接收到挑战图片，后续 POST /verify 将验证新挑战。
        # 因此我们必须在拿到图片后，等待用户输入，然后 POST /verify 验证。
        h2 = {k: v for k, v in _headers("").items() if k != "Content-Type"}
        ir = self.session.get(image_url, headers=h2, timeout=60)
        ir.raise_for_status()
        return sessionid, ir.content

    def verify_figure_code(self, sessionid: str, figure_code: str) -> tuple[bool, bytes | None]:
        """验证图形验证码。返回 (是否通过, 新图片bytes或None)。
        网站流程：先 POST verify 确认图形码正确，再 sendCode 发短信。
        """
        ts = int(time.time() * 1000)
        r = self.session.post(
            f"{BASE}/UserApi/verify",
            headers=_headers(""),
            data=_json_dumps({"sessionid": sessionid, "code": figure_code, "reqtimestamp": ts}),
            timeout=60,
        )
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            body = r.json()
            if body.get("code") == 10000:
                return True, None
            return False, None
        # Wrong code: server returns new image
        return False, r.content

    def send_sms_login_code(self, mobile: str, sessionid: str, figure_code: str) -> None:
        """发送短信验证码。

        直接将用户输入的验证码答案发送到 sendCode，无需额外 verify 步骤。
        """
        ts = int(time.time() * 1000)
        data = {
            "verify": figure_code,
            "mobile": mobile.strip(),
            "sessionid": sessionid,
            "type": "login",
            "secondDomain": "",
            "reqtimestamp": ts,
        }
        r = self.session.post(f"{BASE}/UserApi/sendCode", headers=_headers(""), data=_json_dumps(data), timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            raise RuntimeError(body.get("message") or body.get("msg") or "发送短信失败")

    def login_by_mobile_sms(self, mobile: str, sms_code: str) -> str:
        ts = int(time.time() * 1000)
        data = {
            "mobile": mobile.strip(),
            "code": sms_code.strip(),
            "remember": "0",
            "reqtimestamp": ts,
        }
        r = self.session.post(
            f"{BASE}/UserApi/loginByMobile",
            headers=_headers(""),
            data=_json_dumps(data),
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            raise RuntimeError(body.get("message") or body.get("msg") or "登录失败")
        token = body["data"]["token"]
        self.session.cookies.set("token", token, domain=".ketangpai.com", path="/")
        return token

    def wechat_login_qr(self) -> tuple[str, str]:
        """返回 (二维码图片 URL, code_key)。"""
        ts = int(time.time() * 1000)
        r = self.session.post(f"{BASE}/wechat/login", headers=_headers(""), data=_json_dumps({"reqtimestamp": ts}), timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            raise RuntimeError(body.get("message") or body.get("msg") or "获取微信二维码失败")
        d = body["data"]
        return str(d["url"]), str(d["code_key"])

    def wechat_check_scan(self, code_key: str) -> str | None:
        """微信扫码轮询：code==10000 且 data.token 存在时返回 token，否则返回 None。"""
        ts = int(time.time() * 1000)
        r = self.session.post(
            f"{BASE}/UserApi/checkWechatCode",
            headers=_headers(""),
            data=_json_dumps({"code_key": code_key, "reqtimestamp": ts}),
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            return None
        data = body.get("data") or {}
        tok = data.get("token")
        if tok:
            self.session.cookies.set("token", str(tok), domain=".ketangpai.com", path="/")
            return str(tok)
        return None

    def semester_course_list(
        self,
        token: str,
        semester: str = "",
        term: str = "0",
        search: str = "",
    ) -> list[Course]:
        url = f"{BASE}/CourseApi/semesterCourseList"
        data = {
            "isstudy": "1",
            "search": search,
            "semester": semester,
            "term": term,
            "reqtimestamp": int(time.time() * 1000),
        }
        r = self.session.post(url, headers=_headers(token), data=_json_dumps(data), timeout=60)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            raise RuntimeError(body.get("msg") or "获取课程失败")
        rows = body.get("data") or []
        return [
            Course(
                id=c["id"],
                name=c["coursename"],
                classname=c.get("classname", ""),
                code=c.get("code", ""),
                semester=c.get("semester", ""),
                term=str(c.get("term", "")),
                username=c.get("username", ""),
                total=int(c.get("total", 0)),
                role=int(c.get("role", 0)),
            )
            for c in rows
        ]

    def get_course_content_page(
        self,
        token: str,
        course_id: str,
        dir_id: int,
        area: ContentArea,
        page: int,
        limit: int = 200,
        contenttype: str | None = None,
    ) -> dict[str, Any]:
        if contenttype is None:
            contenttype = "1" if area is ContentArea.COURSEWARE else "2"
        payload: dict[str, Any] = {
            "courseid": course_id,
            "contenttype": contenttype,
            "dirid": dir_id,
            "page": page,
            "limit": limit,
            "name": "",
            "reqtimestamp": int(time.time() * 1000),
        }
        # Try getCourseContent first (the definitive endpoint for materials listing),
        # then fall back to others.  Validate that data is a dict with "list" key.
        for endpoint in ("getCourseContent", "getImportList", "getCourseList"):
            url = f"{BASE}/FutureV2/CourseMeans/{endpoint}"
            r = self.session.post(url, headers=_headers(token), data=_json_dumps(payload), timeout=60)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            body = r.json()
            if body.get("code") == 10000:
                d = body.get("data")
                if isinstance(d, dict) and "list" in d:
                    return body
                # getCourseList returns data as a flat array (not dict with list) — skip
            else:
                pass  # try next endpoint
        raise RuntimeError("获取资料列表失败：所有端点均未返回有效数据")

    def get_course_content_body(
        self,
        token: str,
        course_id: str,
        dir_id: int,
        area: ContentArea,
    ) -> dict[str, Any]:
        merged: dict[str, Any] | None = None
        limit = 200
        content_types: list[str] = ["2", "8"] if area is ContentArea.MATERIALS else ["1"]
        for ct in content_types:
            page = 1
            while True:
                body = self.get_course_content_page(token, course_id, dir_id, area, page=page, limit=limit, contenttype=ct)
                data = body.get("data") or {}
                chunk = data.get("list") or []
                if merged is None:
                    merged = body
                    merged["data"] = dict(data)
                    merged["data"]["list"] = list(chunk)
                else:
                    (merged["data"]["list"]).extend(chunk)
                if len(chunk) < limit:
                    break
                page += 1
        assert merged is not None
        return merged

    def parse_list(self, body: dict[str, Any], course_id: str, area: ContentArea) -> list[FolderItem | FileItem]:
        data = body.get("data") or {}
        raw_list = data.get("list") or []
        out: list[FolderItem | FileItem] = []
        seen_folder_ids: set[str] = set()

        if area is ContentArea.COURSEWARE:
            for row in raw_list:
                fi = _file_item_from_course_row(row, course_id)
                # Courseware uses "directory" key instead of "folder"
                dmeta = row.get("directory")
                if dmeta and isinstance(dmeta, dict) and dmeta.get("id"):
                    fi.folder_id = str(dmeta["id"])
                    fi.folder_title = dmeta.get("title") or ""
                    fi.folder_parent_id_for_nav = _folder_parent_id(dmeta)
                    if fi.folder_id not in seen_folder_ids:
                        seen_folder_ids.add(fi.folder_id)
                        out.append(
                            FolderItem(
                                id=fi.folder_id,
                                name=fi.folder_title or "课件目录",
                                parent_id=_folder_parent_id(dmeta) if isinstance(dmeta, dict) else None,
                            )
                        )
                out.append(fi)
            return out

        # Materials: all items have "folder" key (None for root, dict for subfolder)
        # True folder items don't exist in the API - we create virtual folders from file metadata
        for row in raw_list:
            fi = _file_item_from_course_row(row, course_id)
            fmeta = row.get("folder")
            if isinstance(fmeta, dict) and fmeta.get("id"):
                fi.folder_id = str(fmeta["id"])
                fi.folder_title = fmeta.get("title") or ""
                fi.folder_parent_id_for_nav = _folder_parent_id(fmeta)
                # Create virtual folder entry
                if fi.folder_id not in seen_folder_ids:
                    seen_folder_ids.add(fi.folder_id)
                    out.append(
                        FolderItem(
                            id=fi.folder_id,
                            name=fi.folder_title or "文件夹",
                            parent_id=_folder_parent_id(fmeta),
                        )
                    )
            out.append(fi)
        return out

    def list_dir(
        self,
        token: str,
        course_id: str,
        dir_id: str,
        area: ContentArea,
    ) -> list[FolderItem | FileItem]:
        # dirid parameter is ignored by the API — full list cached per (course_id, area)
        cache_key = (course_id, area)
        items = self._listing_cache.get(cache_key)
        if items is None:
            body = self.get_course_content_body(token, course_id, 0, area)
            items = self.parse_list(body, course_id, area)
            self._listing_cache[cache_key] = items
        return _filter_items_for_directory(items, dir_id)

    def get_slide_images(self, token: str, interactid: str) -> list[str] | None:
        """获取互动课件的幻灯片图片签名 URL 列表。

        返回按页码排序的图片 URL 列表，失败返回 None。
        """
        ts = int(time.time() * 1000)
        payload = {"interactid": interactid, "reqtimestamp": ts}
        r = self.session.post(
            f"{BASE}//PrestudyTaskApi/preStudyList",
            headers=_headers(token),
            data=_json_dumps(payload),
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 10000:
            return None
        data = body.get("data") or {}
        pages = data.get("data") or {}
        urls: list[str] = []
        for k in sorted(pages.keys(), key=int):
            src = pages[k].get("src", "")
            if src:
                url = "https:" + src if src.startswith("//") else src
                urls.append(url)
        return urls or None


def collect_direct_file_tasks(
    client: KetangpaiClient,
    token: str,
    course_id: str,
    area: ContentArea,
    dir_id: str,
    rel_prefix: str,
) -> list[DownloadTask]:
    """仅收集某目录直接子文件（不进入子文件夹），用于「非递归」勾选文件夹。"""
    tasks: list[DownloadTask] = []
    for item in client.list_dir(token, course_id, dir_id, area):
        if isinstance(item, FileItem):
            rel = rel_prefix + item.name
            tasks.append(
                DownloadTask(
                    rel_path=rel,
                    display_name=item.name,
                    raw_url=item.raw_url,
                    file_id=item.id,
                    course_id=course_id,
                )
            )
    return tasks


def collect_recursive_tasks(
    client: KetangpaiClient,
    token: str,
    course_id: str,
    area: ContentArea,
    *,
    dir_id: str = "0",
    rel_prefix: str = "",
    walk: bool = True,
) -> list[DownloadTask]:
    """遍历目录，返回待下载文件任务（资料区递归；课件区为扁平列表）。"""

    tasks: list[DownloadTask] = []
    stack: list[tuple[str, str]] = [(dir_id, rel_prefix)]
    seen_dirs: set[str] = set()

    while stack:
        did, prefix = stack.pop()
        if did in seen_dirs:
            continue
        seen_dirs.add(did)
        items = client.list_dir(token, course_id, did, area)
        for item in items:
            if isinstance(item, FolderItem):
                if walk:
                    sub = prefix + item.name + "/"
                    stack.append((item.id, sub))
                continue
            rel = prefix + item.name
            tasks.append(
                DownloadTask(
                    rel_path=rel,
                    display_name=item.name,
                    raw_url=item.raw_url,
                    file_id=item.id,
                    course_id=course_id,
                )
            )
    return tasks
