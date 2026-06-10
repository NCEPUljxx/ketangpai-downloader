from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any

try:
    import tkinter as tk
    from tkinter import filedialog as _filedialog

    _HAS_TK = True
except ImportError:
    tk = None  # type: ignore
    _filedialog = None  # type: ignore
    _HAS_TK = False

import flet as ft

from ktp_core.client import (
    ContentArea,
    Course,
    DownloadTask,
    FileItem,
    FolderItem,
    KetangpaiClient,
    collect_recursive_tasks,
)
from ktp_core.download_manager import DownloadManager

# Flet 的 Image 必须提供 src；加载真实图片前用 1×1 透明 PNG 占位。
_PLACEHOLDER_IMAGE_SRC = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+lm8kAAAAASUVORK5CYII="
)


def _rel_prefix_from_stack(dir_stack: list[tuple[str, str]]) -> str:
    if len(dir_stack) <= 1:
        return ""
    return "/".join(name for _, name in dir_stack[1:]) + "/"


def _pick_directory_dialog(title: str = "选择下载目录") -> str | None:
    """使用系统原生对话框（桌面端），Android 等不支持的平台返回 None。"""
    if not _HAS_TK:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    path = _filedialog.askdirectory(title=title, parent=root)
    root.destroy()
    return path if path else None


@dataclass
class AppState:
    token: str | None = None
    client: KetangpaiClient = field(default_factory=KetangpaiClient)
    courses: list[Course] = field(default_factory=list)
    current_course: Course | None = None
    area: ContentArea = ContentArea.MATERIALS
    dir_stack: list[tuple[str, str]] = field(default_factory=lambda: [("0", "资料根目录")])
    dir_items: list[FolderItem | FileItem] = field(default_factory=list)
    selected: set[int] = field(default_factory=set)
    download_root: str | None = os.path.join(os.path.expanduser("~"), "Desktop")


async def main(page: ft.Page) -> None:
    page.title = "课堂派资料下载"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(
        color_scheme_seed=ft.Colors.INDIGO,
        use_material3=True,
    )
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.padding = 0
    page.bgcolor = ft.Colors.GREY_50

    if not page.web:
        if page.window.width is None:
            page.window.width = 960
        if page.window.height is None:
            page.window.height = 720
        await page.window.center()

    state = AppState()
    course_ui: dict[str, ft.ListView | None] = {"list": None}

    snack = ft.SnackBar(content=ft.Text(""))
    page.snack_bar = snack

    def toast(msg: str) -> None:
        snack.content = ft.Text(msg)
        snack.open = True
        page.update()

    def run_async(handler: Any, *args: Any, **kwargs: Any) -> None:
        """从控件事件触发协程须提交到 Flet 连接的事件循环；桌面端勿用 asyncio.create_task。"""
        page.run_task(handler, *args, **kwargs)

    # --- login ---
    email_f = ft.TextField(label="邮箱 / 手机号账号", autofocus=True, expand=True)
    pass_f = ft.TextField(label="密码", password=True, can_reveal_password=True, expand=True)
    login_status = ft.Text("", size=12, color=ft.Colors.GREY_700)

    # --- SMS login state ---
    sms_phone_f = ft.TextField(label="手机号", dense=True, expand=True)
    sms_captcha_img = ft.Image(_PLACEHOLDER_IMAGE_SRC, visible=False, width=300, height=100, fit=ft.BoxFit.CONTAIN)
    sms_captcha_f = ft.TextField(label="输入算式计算结果", dense=True, visible=False, expand=True)
    sms_code_f = ft.TextField(label="短信验证码（6位）", dense=True, visible=False, expand=True)
    sms_confirm_btn = ft.FilledButton("确认", visible=False, on_click=lambda e: run_async(sms_send_code, e))
    sms_login_btn = ft.FilledButton("验证并登录", icon=ft.Icons.LOGIN, visible=False, on_click=lambda e: run_async(sms_do_login, e))
    sms_ctx: dict[str, Any] = {"sessionid": "", "phone": "", "captcha_path": ""}

    wechat_ctx: dict[str, Any] = {"poll_gen": 0}

    wechat_qr_img = ft.Image(
        _PLACEHOLDER_IMAGE_SRC,
        visible=False,
        width=220,
        height=220,
        fit=ft.BoxFit.CONTAIN,
    )

    async def _poll_wechat(code_key: str, gen: int) -> None:
        for _ in range(180):
            if wechat_ctx["poll_gen"] != gen:
                return
            tok = await asyncio.to_thread(state.client.wechat_check_scan, code_key)
            if tok:
                if wechat_ctx["poll_gen"] != gen:
                    return
                state.token = tok
                login_status.value = "验证 Token…"
                page.update()
                try:
                    state.client.semester_course_list(state.token, "", "0", "")
                    await open_course_view()
                except Exception as exc:  # noqa: BLE001
                    login_status.value = str(exc)
                    page.update()
                return
            await asyncio.sleep(2)
        if wechat_ctx["poll_gen"] == gen:
            login_status.value = "微信扫码超时，请点击「刷新微信二维码」"
            page.update()

    async def refresh_wechat_qr(_: ft.ControlEvent | None = None) -> None:
        wechat_ctx["poll_gen"] += 1
        gen = wechat_ctx["poll_gen"]
        try:
            url, ck = await asyncio.to_thread(state.client.wechat_login_qr)
        except Exception as exc:  # noqa: BLE001
            toast(str(exc))
            return
        wechat_qr_img.src = url
        wechat_qr_img.visible = True
        login_status.value = "请使用微信扫码（在手机上确认登录）…"
        page.update()
        run_async(_poll_wechat, ck, gen)

    async def do_login_password(_: ft.ControlEvent | None) -> None:
        login_status.value = "登录中…"
        page.update()
        try:
            tok = await asyncio.to_thread(state.client.login_password, email_f.value.strip(), pass_f.value)
            state.token = tok
            await open_course_view()
        except Exception as exc:  # noqa: BLE001
            login_status.value = f"登录失败: {exc}"
            page.update()

    async def sms_get_captcha(_: ft.ControlEvent | None = None) -> None:
        phone = sms_phone_f.value.strip()
        if not phone:
            toast("请先输入手机号")
            return
        login_status.value = "获取验证码…"
        page.update()
        try:
            sid, img_bytes = await asyncio.to_thread(state.client.get_figure_challenge)
            sms_ctx["sessionid"] = sid
            sms_ctx["phone"] = phone
            import tempfile, os as _os, time as _time
            # Clean up old captcha file
            old_path = sms_ctx.get("captcha_path", "")
            if old_path and _os.path.isfile(old_path):
                try:
                    _os.remove(old_path)
                except OSError:
                    pass
            fpath = _os.path.join(tempfile.gettempdir(), f"ktp_captcha_{_time.time_ns()}.png")
            with open(fpath, "wb") as f:
                f.write(img_bytes)
            sms_ctx["captcha_path"] = fpath
            sms_captcha_img.src = fpath
            sms_captcha_img.visible = True
            sms_captcha_f.visible = True
            sms_captcha_f.value = ""
            sms_confirm_btn.visible = True
            sms_code_f.visible = False
            sms_login_btn.visible = False
            login_status.value = "请输入算式计算结果"
            page.update()
        except Exception as exc:  # noqa: BLE001
            import traceback
            login_status.value = f"获取验证码失败: {exc}"
            traceback.print_exc(file=sys.stderr)
            page.update()

    async def sms_send_code(_: ft.ControlEvent | None = None) -> None:
        answer = sms_captcha_f.value.strip()
        if not answer:
            toast("请输入算式计算结果")
            return
        login_status.value = "发送短信…"
        page.update()
        try:
            await asyncio.to_thread(
                state.client.send_sms_login_code,
                sms_ctx["phone"], sms_ctx["sessionid"], answer,
            )
            sms_captcha_img.visible = False
            sms_captcha_f.visible = False
            sms_confirm_btn.visible = False
            sms_code_f.visible = True
            sms_code_f.value = ""
            sms_login_btn.visible = True
            login_status.value = "短信已发送，请输入验证码"
            page.update()
        except Exception as exc:  # noqa: BLE001
            login_status.value = f"发送短信失败: {exc}"
            page.update()

    async def sms_do_login(_: ft.ControlEvent | None = None) -> None:
        code = sms_code_f.value.strip()
        if not code:
            toast("请输入短信验证码")
            return
        login_status.value = "登录中…"
        page.update()
        try:
            tok = await asyncio.to_thread(
                state.client.login_by_mobile_sms,
                sms_ctx["phone"], code,
            )
            state.token = tok
            await open_course_view()
        except Exception as exc:  # noqa: BLE001
            login_status.value = f"登录失败: {exc}"
            page.update()

    login_body = ft.ResponsiveRow(
        [
            ft.Container(
                ft.Card(
                    content=ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Icon(ft.Icons.LOGIN, color=ft.Colors.INDIGO_400, size=22),
                                ft.Text("账号密码", size=15, weight=ft.FontWeight.W_600),
                            ]),
                            ft.Text("使用课堂派账号密码直接登录", size=11, color=ft.Colors.GREY_500),
                            email_f,
                            pass_f,
                            ft.FilledButton("登录", icon=ft.Icons.LOGIN, on_click=lambda e: run_async(do_login_password, e)),
                        ], spacing=10),
                        padding=20,
                    ),
                    elevation=1,
                ),
                col={"sm": 12, "md": 4},
            ),
            ft.Container(
                ft.Card(
                    content=ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Icon(ft.Icons.SMS, color=ft.Colors.INDIGO_400, size=22),
                                ft.Text("手机验证码", size=15, weight=ft.FontWeight.W_600),
                            ]),
                            ft.Text("输入手机号获取短信验证码", size=11, color=ft.Colors.GREY_500),
                            sms_phone_f,
                            ft.FilledButton("发送验证码", icon=ft.Icons.SMS, on_click=lambda e: run_async(sms_get_captcha, e)),
                            sms_captcha_img,
                            sms_captcha_f,
                            sms_confirm_btn,
                            sms_code_f,
                            sms_login_btn,
                        ], spacing=10),
                        padding=20,
                    ),
                    elevation=1,
                ),
                col={"sm": 12, "md": 4},
            ),
            ft.Container(
                ft.Card(
                    content=ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Icon(ft.Icons.WECHAT, color=ft.Colors.INDIGO_400, size=22),
                                ft.Text("微信扫码", size=15, weight=ft.FontWeight.W_600),
                            ]),
                            ft.Text("使用微信扫描二维码登录", size=11, color=ft.Colors.GREY_500),
                            ft.Row([wechat_qr_img], alignment=ft.MainAxisAlignment.CENTER),
                            ft.Row([
                                ft.OutlinedButton("刷新二维码", icon=ft.Icons.REFRESH, on_click=lambda e: run_async(refresh_wechat_qr, e)),
                            ], alignment=ft.MainAxisAlignment.CENTER),
                        ], spacing=10),
                        padding=20,
                    ),
                    elevation=1,
                ),
                col={"sm": 12, "md": 4},
            ),
        ],
        spacing=16,
        run_spacing=16,
    )

    login_view = ft.Column(
        [
            ft.Container(height=30),
            ft.Row([
                ft.Icon(ft.Icons.SCHOOL, size=40, color=ft.Colors.INDIGO_400),
                ft.Column([
                    ft.Text("课堂派资料下载", size=28, weight=ft.FontWeight.W_700),
                    ft.Text("支持账号密码、手机验证码、微信扫码登录", size=13, color=ft.Colors.GREY_600),
                ], spacing=2),
            ], alignment=ft.MainAxisAlignment.CENTER),
            ft.Container(height=10),
            ft.Container(
                content=login_body,
                expand=True,
            ),
            ft.Container(
                content=login_status,
                alignment=ft.Alignment(x=0.5, y=0),
                padding=8,
            ),
        ],
        expand=True,
    )

    # --- courses ---
    search_f = ft.TextField(label="搜索课程", dense=True, expand=True)

    empty_hint = ft.Text("暂无课程，可尝试搜索关键词", size=13, color=ft.Colors.GREY_700)

    async def refresh_courses() -> None:
        lv = course_ui["list"]
        if not state.token or lv is None:
            return
        try:
            state.courses = await asyncio.to_thread(
                state.client.semester_course_list,
                state.token,
                "",
                "0",
                search_f.value.strip(),
            )
            lv.controls.clear()
            if not state.courses:
                empty_hint.visible = True
                page.update()
                return
            empty_hint.visible = False
            TERM_LABELS = {"1": "上学期", "2": "下学期"}
            grouped: dict[str, dict[str, list[Course]]] = {}
            for c in state.courses:
                sem = c.semester or "未知学年"
                t = c.term or "0"
                grouped.setdefault(sem, {}).setdefault(t, []).append(c)
            for sem in sorted(grouped, reverse=True):
                term_data = grouped[sem]
                total_count = sum(len(v) for v in term_data.values())
                term_tiles = []
                for t in ["2", "1", "0"]:
                    if t not in term_data:
                        continue
                    courses_in_term = term_data[t]
                    term_name = TERM_LABELS.get(t, "其他")
                    course_cards = []
                    for c in courses_in_term:
                        detail_parts = [c.classname] if c.classname else []
                        if c.code:
                            detail_parts.append(f"加课码: {c.code}")
                        if c.username:
                            detail_parts.append(f"负责人: {c.username}")
                        subtitle = "  |  ".join(detail_parts)
                        # Use Container.on_click for reliable event handling inside ExpansionTile
                        course_cards.append(
                            ft.Container(
                                content=ft.Row(
                                    [
                                        ft.Container(
                                            content=ft.Icon(ft.Icons.SCHOOL_OUTLINED, color=ft.Colors.INDIGO_400, size=22),
                                            padding=10,
                                            border_radius=12,
                                            bgcolor=ft.Colors.INDIGO_50,
                                        ),
                                        ft.Column(
                                            [
                                                ft.Text(c.name, weight=ft.FontWeight.W_600, size=15),
                                                ft.Text(subtitle, size=12, color=ft.Colors.GREY_600) if subtitle else ft.Text(""),
                                            ],
                                            expand=True,
                                            spacing=2,
                                        ),
                                        ft.Icon(ft.Icons.CHEVRON_RIGHT, color=ft.Colors.GREY_400, size=22),
                                    ],
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                padding=14,
                                margin=ft.Margin(4, 3, 4, 3),
                                border_radius=12,
                                bgcolor=ft.Colors.SURFACE,
                                shadow=ft.BoxShadow(blur_radius=2, spread_radius=0, color=ft.Colors.GREY_200),
                                ink=True,
                                on_click=lambda e, cid=c.id: run_async(_open_course_by_id, cid),
                            )
                        )
                    term_tiles.append(
                        ft.ExpansionTile(
                            title=ft.Text(f"{term_name}  ({len(courses_in_term)}门)", weight=ft.FontWeight.W_500, size=14),
                            expanded=False,
                            controls=course_cards,
                            bgcolor=ft.Colors.GREY_100,
                            collapsed_bgcolor=ft.Colors.GREY_100,
                        )
                    )
                lv.controls.append(
                    ft.ExpansionTile(
                        title=ft.Text(f"{sem}学年  ({total_count}门)", weight=ft.FontWeight.W_700, size=15),
                        expanded=False,
                        controls=term_tiles,
                    )
                )
            page.update()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            toast(f"获取课程列表失败: {exc}")
            empty_hint.visible = True
            page.update()

    async def _open_course_by_id(course_id: str) -> None:
        for i, c in enumerate(state.courses):
            if c.id == course_id:
                await open_materials_for_course(i)
                return

    async def open_course_view() -> None:
        course_list = ft.ListView(
            spacing=6,
            padding=4,
            expand=True,
        )
        course_ui["list"] = course_list
        page.controls.clear()
        page.add(
            ft.Column(
                [
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.SCHOOL, size=28, color=ft.Colors.ON_SURFACE_VARIANT),
                            ft.Text("我的课程", size=22, weight=ft.FontWeight.W_700),
                            ft.IconButton(
                                icon=ft.Icons.LOGOUT,
                                tooltip="退出",
                                on_click=lambda _: run_async(logout),
                            ),
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        padding=ft.Padding(16, 12, 16, 8),
                        bgcolor=ft.Colors.SURFACE,
                    ),
                    ft.Container(
                        content=ft.Row([
                            search_f,
                            ft.FilledButton("搜索", icon=ft.Icons.SEARCH, on_click=lambda _: run_async(refresh_courses)),
                            ft.FilledButton("刷新", icon=ft.Icons.REFRESH, on_click=lambda _: run_async(refresh_courses)),
                        ]),
                        padding=ft.Padding(16, 0, 16, 8),
                    ),
                    ft.Divider(height=1, color=ft.Colors.GREY_200),
                    ft.Container(empty_hint, padding=ft.Padding.only(top=40), alignment=ft.Alignment(x=0.5, y=0)),
                    ft.Container(
                        content=course_list,
                        expand=True,
                        padding=12,
                    ),
                ],
                expand=True,
                spacing=0,
            )
        )
        await refresh_courses()
        page.update()

    search_f.on_submit = lambda _: run_async(refresh_courses)

    async def logout() -> None:
        wechat_ctx["poll_gen"] += 1
        state.client = KetangpaiClient()
        wechat_qr_img.src = _PLACEHOLDER_IMAGE_SRC
        wechat_qr_img.visible = False
        state.token = None
        state.courses.clear()
        state.current_course = None
        page.controls.clear()
        page.add(login_view)
        page.update()

    area_seg = ft.SegmentedButton(
        selected=[ContentArea.MATERIALS.value],
        allow_multiple_selection=False,
        on_change=lambda e: run_async(on_area_change, e),
        segments=[
            ft.Segment(value=ContentArea.MATERIALS.value, label=ft.Text("资料区")),
            ft.Segment(value=ContentArea.COURSEWARE.value, label=ft.Text("互动课件")),
        ],
    )

    breadcrumb_row = ft.Row(wrap=True, spacing=6)
    items_column = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
    download_dir_f = ft.TextField(
        label="下载保存目录（可手动粘贴路径）",
        dense=True,
        expand=True,
        hint_text=r"例如 D:\Downloads\课堂派",
    )
    async def pick_download_dir(_: ft.ControlEvent | None) -> None:
        path = await asyncio.to_thread(_pick_directory_dialog, "选择下载目录")
        if path:
            state.download_root = path
            download_dir_f.value = path
            page.update()
        else:
            toast("未选择目录")

    def sync_download_path_from_field(_: ft.ControlEvent | None = None) -> None:
        p = (download_dir_f.value or "").strip()
        state.download_root = p if p else None
        page.update()

    download_dir_f.on_blur = sync_download_path_from_field
    download_dir_f.on_submit = lambda _: sync_download_path_from_field()

    async def on_area_change(e: ft.ControlEvent) -> None:
        sel = e.control.selected
        if not sel:
            return
        v = sel[0]
        state.area = ContentArea(v)
        state.dir_stack = [("0", "资料根目录")]
        state.selected.clear()
        await reload_current_dir()

    async def navigate_to_folder(folder_id: str, folder_name: str) -> None:
        state.dir_stack.append((folder_id, folder_name))
        state.selected.clear()
        await reload_current_dir()

    async def breadcrumb_jump(idx: int) -> None:
        state.dir_stack = state.dir_stack[: idx + 1]
        state.selected.clear()
        await reload_current_dir()

    async def open_materials_for_course(index: int) -> None:
        c = state.courses[index]
        state.current_course = c
        state.client.retain_listing_cache_for_course_only(c.id)
        state.area = ContentArea.MATERIALS
        state.dir_stack = [("0", "资料根目录")]
        state.selected.clear()
        area_seg.selected = [ContentArea.MATERIALS.value]
        await show_materials_page()

    def rebuild_breadcrumb() -> None:
        breadcrumb_row.controls.clear()
        for i, (_, name) in enumerate(state.dir_stack):
            if i > 0:
                breadcrumb_row.controls.append(ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16))
            breadcrumb_row.controls.append(
                ft.TextButton(
                    content=ft.Text(name, size=13),
                    style=ft.ButtonStyle(padding=4),
                    on_click=lambda e, ii=i: run_async(breadcrumb_jump, ii),
                )
            )

    def on_item_checkbox(e: ft.ControlEvent, ii: int) -> None:
        if e.control.value:
            state.selected.add(ii)
        else:
            state.selected.discard(ii)

    def select_all_page(_: ft.ControlEvent | None) -> None:
        state.selected = set(range(len(state.dir_items)))
        rebuild_items()
        page.update()

    def clear_selection(_: ft.ControlEvent | None) -> None:
        state.selected.clear()
        rebuild_items()
        page.update()

    def _format_size(size: int) -> str:
        if size <= 0:
            return ""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"

    def rebuild_items() -> None:
        items_column.controls.clear()
        for i, it in enumerate(state.dir_items):
            if isinstance(it, FolderItem):
                icon = ft.Icons.FOLDER
                subtitle = "文件夹"
                trailing = ft.IconButton(
                    icon=ft.Icons.CHEVRON_RIGHT,
                    tooltip="进入文件夹",
                    on_click=lambda e, ii=i: run_async(_enter_folder, ii),
                )
            else:
                icon = ft.Icons.INSERT_DRIVE_FILE_OUTLINED
                sz = _format_size(it.filesize)
                subtitle = f"文件{f'  |  {sz}' if sz else ''}"
                trailing = ft.IconButton(
                    icon=ft.Icons.DOWNLOAD,
                    tooltip="下载此文件",
                    on_click=lambda e, ii=i: run_async(_download_single, ii),
                )
            cb = ft.Checkbox(value=i in state.selected, on_change=lambda e, ii=i: on_item_checkbox(e, ii))

            items_column.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            cb,
                            ft.Container(
                                content=ft.Icon(icon, color=ft.Colors.INDIGO_400, size=20),
                                padding=8,
                                border_radius=10,
                                bgcolor=ft.Colors.INDIGO_50 if isinstance(it, FolderItem) else ft.Colors.GREY_100,
                            ),
                            ft.Column(
                                [
                                    ft.Text(it.name, weight=ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                    ft.Text(subtitle, size=11, color=ft.Colors.GREY_500),
                                ],
                                expand=True,
                                spacing=0,
                            ),
                            trailing,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=12,
                    margin=ft.Margin(0, 2, 0, 2),
                    border_radius=10,
                    bgcolor=ft.Colors.SURFACE,
                    shadow=ft.BoxShadow(blur_radius=1, spread_radius=0, color=ft.Colors.GREY_200),
                )
            )

    async def _enter_folder(idx: int) -> None:
        item = state.dir_items[idx]
        if isinstance(item, FolderItem):
            await navigate_to_folder(item.id, item.name)

    async def _download_single(idx: int) -> None:
        item = state.dir_items[idx]
        if not isinstance(item, FileItem):
            return
        sync_download_path_from_field()
        if not state.download_root:
            toast("请先选择下载目录")
            return
        assert state.current_course
        prefix = _rel_prefix_from_stack(state.dir_stack)
        task = DownloadTask(
            rel_path=prefix + item.name,
            display_name=item.name,
            raw_url=item.raw_url,
            file_id=item.id,
            course_id=state.current_course.id,
        )
        await _run_download([task])

    async def reload_current_dir(*, invalidate_cache: bool = False) -> None:
        assert state.token and state.current_course
        rebuild_breadcrumb()
        try:
            cur_id = state.dir_stack[-1][0]
            if invalidate_cache:
                state.client.invalidate_course_list_cache(state.current_course.id, state.area)
            state.dir_items = await asyncio.to_thread(
                state.client.list_dir,
                state.token,
                state.current_course.id,
                cur_id,
                state.area,
            )
            rebuild_items()
            page.update()
        except Exception as exc:  # noqa: BLE001
            toast(f"获取目录内容失败: {exc}")
            page.update()

    async def show_materials_page() -> None:
        assert state.current_course
        if state.download_root:
            download_dir_f.value = state.download_root
        page.controls.clear()
        c = state.current_course
        page.add(
            ft.Column(
                [
                    ft.Container(
                        content=ft.Row([
                            ft.IconButton(icon=ft.Icons.ARROW_BACK, on_click=lambda _: run_async(open_course_view)),
                            ft.Text(c.name, size=18, weight=ft.FontWeight.W_700, expand=True),
                        ]),
                        padding=ft.Padding(8, 8, 8, 0),
                        bgcolor=ft.Colors.SURFACE,
                    ),
                    ft.Container(
                        content=ft.Column([
                            area_seg,
                            ft.Container(content=breadcrumb_row, padding=ft.Padding.symmetric(vertical=4)),
                        ]),
                        padding=ft.Padding(12, 8, 12, 4),
                    ),
                    ft.Divider(height=1, color=ft.Colors.GREY_200),
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.FilledButton("下载选中项", icon=ft.Icons.DOWNLOAD, on_click=lambda e: run_async(download_selected, e)),
                                ft.FilledButton("全选本页", icon=ft.Icons.CHECKLIST, on_click=select_all_page),
                                ft.OutlinedButton("清除选择", icon=ft.Icons.CLEAR_ALL, on_click=clear_selection),
                                ft.OutlinedButton("刷新", icon=ft.Icons.REFRESH, on_click=lambda _: run_async(reload_current_dir, invalidate_cache=True)),
                            ], wrap=True, spacing=8),
                            ft.Row([
                                download_dir_f,
                                ft.FilledButton("浏览文件夹", icon=ft.Icons.FOLDER_OPEN, on_click=lambda _: run_async(pick_download_dir, None)),
                            ], spacing=8),
                            ft.Text(
                                state.download_root or "请选择下载目录",
                                size=11,
                                color=ft.Colors.GREY_500 if state.download_root else ft.Colors.RED_300,
                                italic=not state.download_root,
                            ),
                        ], spacing=8),
                        padding=ft.Padding(12, 8, 12, 8),
                    ),
                    ft.Container(
                        content=items_column,
                        expand=True,
                        padding=ft.Padding(12, 0, 12, 12),
                    ),
                ],
                expand=True,
                spacing=0,
            )
        )
        await reload_current_dir()
        page.update()

    def build_tasks_for_selected() -> list[DownloadTask]:
        assert state.token and state.current_course
        prefix = _rel_prefix_from_stack(state.dir_stack)
        tasks: list[DownloadTask] = []
        for i in sorted(state.selected):
            if i < 0 or i >= len(state.dir_items):
                continue
            item = state.dir_items[i]
            if isinstance(item, FileItem):
                tasks.append(
                    DownloadTask(
                        rel_path=prefix + item.name,
                        display_name=item.name,
                        raw_url=item.raw_url,
                        file_id=item.id,
                        course_id=state.current_course.id,
                    )
                )
            elif isinstance(item, FolderItem):
                sub_prefix = prefix + item.name + "/"
                tasks.extend(
                    collect_recursive_tasks(
                        state.client,
                        state.token,
                        state.current_course.id,
                        state.area,
                        dir_id=item.id,
                        rel_prefix=sub_prefix,
                        walk=True,
                    )
                )
        return tasks

    async def _run_download(tasks: list[DownloadTask]) -> None:
        """Shared download runner with progress UI. Thread-safe via polling."""

        def _pb_numeric_value(pb: ft.ProgressBar) -> float:
            raw = getattr(pb, "value", None)
            if raw is None:
                return 0.0
            try:
                return float(raw)
            except (TypeError, ValueError):
                return 0.0

        if not tasks:
            return
        # Auto-create course subfolder under download root
        course_name = state.current_course.name if state.current_course else "下载"
        safe_course = re.sub(r'[\\/:*?"<>|]', '_', course_name).strip()
        download_root = os.path.join(state.download_root, safe_course)
        os.makedirs(download_root, exist_ok=True)
        progress_bars: dict[str, ft.ProgressBar] = {}
        progress_col = ft.Column(spacing=8, expand=True, scroll=ft.ScrollMode.AUTO)
        for t in tasks:
            pb = ft.ProgressBar(width=400, value=0.0)
            progress_bars[t.rel_path] = pb
            progress_col.controls.append(
                ft.Column([
                    ft.Text(t.display_name, size=12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    pb,
                ], spacing=2)
            )
        status_text = ft.Text("准备下载...", size=12, color=ft.Colors.GREY_600)
        progress_col.controls.append(ft.Divider(height=1))
        progress_col.controls.append(status_text)
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.DOWNLOADING, color=ft.Colors.INDIGO_400, size=24),
                ft.Text(f"下载中 ({len(tasks)} 个文件)"),
            ]),
            content=ft.Container(content=progress_col, width=480, height=380),
        )
        page.show_dialog(dlg)
        page.update()

        lock = threading.Lock()
        progress_data: dict[str, tuple[float, str | None]] = {}
        done_flag = threading.Event()

        def on_progress(key: str, fraction: float, current: int, total: int, status: str | None) -> None:
            with lock:
                progress_data[key] = (fraction, status)

        def work() -> list[Any]:
            try:
                mgr = DownloadManager(max_workers=6, retries=2, copy_cookies_from=state.client.session, slide_fallback_cookies=state.client.session)
                return mgr.run_tasks(tasks, download_root, progress=on_progress)
            finally:
                done_flag.set()

        async def poll_progress() -> None:
            while not done_flag.is_set():
                try:
                    with lock:
                        for key, (frac, _st) in progress_data.items():
                            pb = progress_bars.get(key)
                            if pb:
                                pb.value = frac
                        completed = sum(1 for p in progress_bars.values() if _pb_numeric_value(p) >= 0.999)
                        status_text.value = f"已完成: {completed}/{len(progress_bars)}"
                    page.update()
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                await asyncio.sleep(0.1)
            try:
                with lock:
                    for key, (frac, _st) in progress_data.items():
                        pb = progress_bars.get(key)
                        if pb:
                            pb.value = frac
                page.update()
            except Exception:
                traceback.print_exc(file=sys.stderr)

        poll_task = asyncio.create_task(poll_progress())
        results: list[Any] | None = None
        try:
            results = await asyncio.to_thread(work)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            toast(f"下载失败: {exc}")
        finally:
            done_flag.set()
            try:
                await asyncio.wait_for(poll_task, timeout=30.0)
            except Exception:
                pass
            await asyncio.sleep(0.08)
            try:
                page.pop_dialog()
            except Exception:
                pass
            page.update()

        if not results:
            return

        ok = sum(1 for r in results if r.ok)
        bad = [r for r in results if not r.ok]
        msg = f"下载完成: {ok}/{len(results)} 成功"
        if bad:
            msg += "，" + "; ".join(f"{r.name}: {r.error}" for r in bad[:3])
        toast(msg)

    async def download_selected(_: ft.ControlEvent | None) -> None:
        sync_download_path_from_field()
        if not state.download_root:
            toast("请先选择下载目录")
            return
        tasks = build_tasks_for_selected()
        if not tasks:
            toast("请先勾选要下载的文件或文件夹")
            return
        await _run_download(tasks)

    page.add(login_view)


def run_app() -> None:
    ft.run(main, name="课堂派资料下载")


if __name__ == "__main__":
    run_app()
