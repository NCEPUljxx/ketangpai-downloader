"""
课堂派资料下载 - Kivy Android
"""
import os, sys, re, threading, traceback, tempfile, time

# ── Android SSL 证书修复 ──
# certifi 用 pip 安装会导致 recipe 冲突，改用本地文件。
_CACERT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cacert.pem")
if os.path.exists(_CACERT_PATH):
    os.environ["SSL_CERT_FILE"] = _CACERT_PATH
elif not os.environ.get("SSL_CERT_FILE"):
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.checkbox import CheckBox
from kivy.uix.image import Image as KivyImage
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.metrics import dp, sp
from kivy.utils import get_color_from_hex as hex_color
from kivy.core.text import LabelBase

from ktp_core.client import (
    KetangpaiClient, ContentArea, Course, DownloadTask,
    FileItem, FolderItem, collect_recursive_tasks,
)
from ktp_core.download_manager import DownloadManager

INDIGO = hex_color("#3F51B5")
WHITE = hex_color("#FFFFFF")
GREY_TEXT = hex_color("#757575")

# ── Font: use system default + bundled CJK as fallback ──
_FONT_NAME = None
def _find_font():
    global _FONT_NAME
    if _FONT_NAME: return _FONT_NAME
    base = os.path.dirname(os.path.abspath(__file__))
    # Kivy default is Roboto (Latin OK, no CJK)
    # Register bundled DroidSansFallback as CJK fallback
    for try_path in [
        os.path.join(base, "DroidSansFallback.ttf"),
        os.path.join(os.getcwd(), "DroidSansFallback.ttf"),
    ]:
        if os.path.exists(try_path):
            try:
                LabelBase.register("CJK", try_path)
                _FONT_NAME = "CJK"
                return "CJK"
            except Exception:
                pass
    # No bundled font found - try system CJK font (may lack Latin)
    for try_path in [
        "/system/fonts/DroidSansFallback.ttf",
        "/system/fonts/NotoSansCJK-Regular.ttc",
    ]:
        if os.path.exists(try_path):
            try:
                LabelBase.register("CJK", try_path)
                _FONT_NAME = "CJK"
                return "CJK"
            except Exception:
                pass
    _FONT_NAME = "Roboto"
    return "Roboto"

def FONT(): return _find_font()

# ── Helpers ──
def btn(text, cb=None, color=None, h=dp(44), sx=1):
    b = Button(text=text, font_name=FONT(), font_size=sp(14),
               size_hint_y=None, size_hint_x=sx, height=h)
    if color: b.background_color = color
    if cb: b.bind(on_press=cb)
    return b

def lbl(text, fs=sp(13), color=None, **kw):
    return Label(text=text, font_name=FONT(), font_size=fs,
                 color=color or GREY_TEXT, halign="left", valign="middle", **kw)

def inp(hint="", pw=False, h=dp(44)):
    return TextInput(hint_text=hint, multiline=False, password=pw,
                     font_name=FONT(), font_size=sp(14),
                     size_hint_y=None, height=h)


class LoginScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = KetangpaiClient()
        self.token = None
        self.wechat_gen = 0
        self.sms_ctx = {}

        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(4))
        root.add_widget(lbl("课堂派资料下载", sp(20), INDIGO, size_hint_y=None, height=dp(44)))

        tabs = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(4))
        for name, idx in [("账号密码",0),("手机验证码",1),("微信扫码",2)]:
            b = btn(name, h=dp(38), color=INDIGO)
            b.bind(on_press=lambda _, i=idx: self._tab(i))
            tabs.add_widget(b)
        root.add_widget(tabs)

        # Spacer pushes panel to center
        root.add_widget(BoxLayout(size_hint_y=1))
        self.panel = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        root.add_widget(self.panel)
        root.add_widget(BoxLayout(size_hint_y=1))

        # Pwd panel
        self.pwd = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.email_f = inp("邮箱 / 手机号账号")
        self.pass_f = inp("密码", pw=True)
        self.pwd.add_widget(self.email_f)
        self.pwd.add_widget(self.pass_f)
        self.pwd.add_widget(btn("登录", self._login_pwd, INDIGO))

        # SMS panel
        self.sms_pn = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        self.sms_phone = inp("手机号", h=dp(40))
        self.sms_pn.add_widget(self.sms_phone)
        self.sms_captcha_img = KivyImage(size_hint_y=None, height=dp(80))
        self.sms_pn.add_widget(self.sms_captcha_img)
        self.sms_captcha_inp = inp("算式计算结果", h=dp(40))
        self.sms_pn.add_widget(self.sms_captcha_inp)
        self.sms_code_inp = inp("短信验证码(6位)", h=dp(40))
        self.sms_pn.add_widget(self.sms_code_inp)
        self.sms_send_btn = btn("获取验证码", self._sms_get, INDIGO, h=dp(40))
        self.sms_pn.add_widget(self.sms_send_btn)
        self.sms_login_btn = btn("验证并登录", self._sms_do, h=dp(40))
        self.sms_pn.add_widget(self.sms_login_btn)

        # WeChat panel
        self.wx_pn = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.wx_qr = KivyImage(size_hint_y=None, height=dp(250))
        self.wx_pn.add_widget(self.wx_qr)
        self.wx_pn.add_widget(btn("刷新微信二维码", self._wx_refresh, INDIGO))

        self.st = lbl("", sp(11), size_hint_y=None, height=dp(20))
        root.add_widget(self.st)
        self.add_widget(root)
        self._tab(0)

    def _tab(self, idx):
        self.panel.clear_widgets()
        self.panel.add_widget([self.pwd, self.sms_pn, self.wx_pn][idx])

    def _status(self, msg):
        Clock.schedule_once(lambda dt: setattr(self.st, "text", msg))

    # ── Password ──
    def _login_pwd(self, _):
        e, p = self.email_f.text.strip(), self.pass_f.text
        if not e or not p: return self._status("请输入账号和密码")
        self._status("登录中...")
        threading.Thread(target=self._pwd_thread, args=(e,p), daemon=True).start()

    def _pwd_thread(self, e, p):
        try:
            tok = self.client.login_password(e, p)
            self.token = tok
            self._goto_courses()
        except Exception as ex:
            self._status(f"登录失败: {ex}")

    @mainthread
    def _goto_courses(self):
        self.manager.get_screen("courses").set_token(self.token, self.client)
        self.manager.current = "courses"

    # ── SMS ──
    def _sms_get(self, _):
        phone = self.sms_phone.text.strip()
        if not phone: return self._status("请先输入手机号")
        self._status("获取验证码...")
        threading.Thread(target=self._sms_captcha_thread, args=(phone,), daemon=True).start()

    def _sms_captcha_thread(self, phone):
        try:
            sid, img = self.client.get_figure_challenge()
            self.sms_ctx["sid"], self.sms_ctx["phone"] = sid, phone
            fp = os.path.join(tempfile.gettempdir(), f"ktp_c_{time.time_ns()}.png")
            with open(fp, "wb") as f: f.write(img)
            self.sms_ctx["captcha"] = fp
            Clock.schedule_once(lambda dt: self._on_captcha(fp))
        except Exception as ex:
            self._status(f"获取失败: {ex}")

    @mainthread
    def _on_captcha(self, fp):
        self.sms_captcha_img.source = fp
        self.sms_captcha_img.reload()
        self.sms_captcha_inp.text = ""
        self.sms_send_btn.text = "发送短信验证码"
        self.sms_send_btn.unbind(on_press=self._sms_get)
        self.sms_send_btn.bind(on_press=self._sms_send)
        self._status("输入算式结果后点击发送短信")

    def _sms_send(self, _):
        ans = self.sms_captcha_inp.text.strip()
        if not ans: return self._status("请输入算式结果")
        self._status("发送中...")
        threading.Thread(target=self._sms_send_thread, args=(ans,), daemon=True).start()

    def _sms_send_thread(self, ans):
        try:
            self.client.send_sms_login_code(self.sms_ctx["phone"], self.sms_ctx["sid"], ans)
            self._status("短信已发送，输入验证码后点登录")
        except Exception as ex:
            self._status(f"发送失败: {ex}")

    def _sms_do(self, _):
        code = self.sms_code_inp.text.strip()
        if not code: return self._status("请输入验证码")
        self._status("登录中...")
        threading.Thread(target=self._sms_do_thread, args=(code,), daemon=True).start()

    def _sms_do_thread(self, code):
        try:
            tok = self.client.login_by_mobile_sms(self.sms_ctx["phone"], code)
            self.token = tok
            self._goto_courses()
        except Exception as ex:
            self._status(f"登录失败: {ex}")

    # ── WeChat ──
    def _wx_refresh(self, _=None):
        self.wechat_gen += 1
        g = self.wechat_gen
        self._status("加载中...")
        threading.Thread(target=self._wx_thread, args=(g,), daemon=True).start()

    def _wx_thread(self, g):
        try:
            url, ck = self.client.wechat_login_qr()
            import requests as req
            resp = req.get(url, timeout=30)
            fp = os.path.join(tempfile.gettempdir(), f"ktp_wx_{g}.png")
            with open(fp, "wb") as f: f.write(resp.content)
            Clock.schedule_once(lambda dt: self._on_wx(fp, ck, g))
        except Exception as ex:
            self._status(f"获取二维码失败: {ex}")

    @mainthread
    def _on_wx(self, fp, ck, g):
        self.wx_qr.source = fp
        self.wx_qr.reload()
        self._status("请用微信扫码确认...")
        threading.Thread(target=self._wx_poll, args=(ck, g), daemon=True).start()

    def _wx_poll(self, ck, g):
        for _ in range(180):
            if self.wechat_gen != g: return
            try:
                tok = self.client.wechat_check_scan(ck)
                if tok:
                    self.token = tok
                    self._status("验证中...")
                    self.client.semester_course_list(self.token, "", "0", "")
                    self._goto_courses()
                    return
            except Exception:
                pass
            time.sleep(2)
        if self.wechat_gen == g:
            self._status("微信扫码超时，请刷新")


class CourseListScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.token = None
        self.client = None
        self.courses = []

        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        hdr = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        hdr.add_widget(lbl("我的课程", sp(18), INDIGO, size_hint_x=1))
        hdr.add_widget(btn("退出", self._logout, h=dp(36), sx=None, color=None))
        root.add_widget(hdr)

        sr = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        self.search_f = inp("搜索课程", h=dp(38))
        sr.add_widget(self.search_f)
        sr.add_widget(btn("搜索", lambda x: self._refresh(), h=dp(38), sx=None))
        sr.add_widget(btn("刷新", lambda x: self._refresh(), h=dp(38), sx=None))
        root.add_widget(sr)

        self.scroll = ScrollView()
        self.course_box = BoxLayout(orientation="vertical", spacing=dp(2), size_hint_y=None)
        self.course_box.bind(minimum_height=self.course_box.setter("height"))
        self.scroll.add_widget(self.course_box)
        root.add_widget(self.scroll)

        self.empty = lbl("暂无课程", sp(13), size_hint_y=None, height=dp(40))
        root.add_widget(self.empty)
        self.add_widget(root)

    def set_token(self, tok, cli):
        self.token, self.client = tok, cli
        self._refresh()

    def _refresh(self):
        if not self.token: return
        threading.Thread(target=self._refresh_thread, daemon=True).start()

    def _refresh_thread(self):
        try:
            cs = self.client.semester_course_list(self.token, "", "0", self.search_f.text.strip())
            Clock.schedule_once(lambda dt: self._on_courses(cs))
        except Exception as e:
            Clock.schedule_once(lambda dt: setattr(self.empty, "text", f"错误: {e}"))

    @mainthread
    def _on_courses(self, cs):
        self.courses = cs
        self.course_box.clear_widgets()
        if not cs:
            self.empty.text = "暂无课程"
            return
        self.empty.text = ""
        TERM_LABELS = {"1": "上学期", "2": "下学期"}

        grouped = {}
        for c in cs:
            sem = c.semester or "未知学年"
            term = c.term or "0"
            grouped.setdefault(sem, {}).setdefault(term, []).append(c)

        for sem in sorted(grouped, reverse=True):
            term_data = grouped[sem]
            total = sum(len(v) for v in term_data.values())

            # ---- SEMESTER YEAR HEADER ----
            sem_btn = Button(
                text=f"+ {sem}学年 ({total}门)",
                font_name=FONT(), font_size=sp(14),
                size_hint_y=None, height=dp(40), halign="left", valign="middle",
            )
            self.course_box.add_widget(sem_btn)
            sem_term_widgets = []  # term headers
            sem_term_widgets_raw = []  # (term_btn, h, term_courses_list)

            for t in ["2", "1", "0"]:
                if t not in term_data: continue
                term_name = TERM_LABELS.get(t, "其他")
                courses_in_term = term_data[t]

                # ---- TERM HEADER ----
                term_btn = Button(
                    text=f"    + {term_name} ({len(courses_in_term)}门)",
                    font_name=FONT(), font_size=sp(12),
                    size_hint_y=None, height=dp(0), opacity=0,
                    halign="left", valign="middle",
                )
                self.course_box.add_widget(term_btn)
                sem_term_widgets.append((term_btn, dp(32)))
                term_courses = []
                sem_term_widgets_raw.append((term_btn, dp(32), term_courses))

                for c in courses_in_term:
                    sub = " | ".join(filter(None, [c.classname, f"课码:{c.code}" if c.code else ""]))
                    cbtn = Button(
                        text=f"        {c.name}" + (f"\n        {sub}" if sub else ""),
                        font_name=FONT(), font_size=sp(11),
                        size_hint_y=None, height=dp(0), opacity=0,
                        halign="left", valign="middle",
                    )
                    cbtn.bind(on_press=lambda btn, cid=c.id: self._open(cid))
                    self.course_box.add_widget(cbtn)
                    term_courses.append((cbtn, dp(46) if sub else dp(34)))

                # Term toggle: only toggles courses, not self
                def make_term_toggle(tbtn, tcourse_list):
                    def toggle(instance):
                        if getattr(tbtn, "texpanded", False):
                            for w, h in tcourse_list:
                                w.height = 0; w.opacity = 0; w.disabled = True
                            tbtn.text = tbtn.text.replace("-", "+")
                            tbtn.texpanded = False
                        else:
                            for w, h in tcourse_list:
                                w.height = h; w.opacity = 1; w.disabled = False
                            tbtn.text = tbtn.text.replace("+", "-")
                            tbtn.texpanded = True
                    return toggle
                term_btn.texpanded = False
                term_btn.bind(on_press=make_term_toggle(term_btn, term_courses))

            # Semester toggle: hide term headers AND their courses
            def make_sem_toggle(sbtn, sterms, all_course_widgets):
                def toggle(instance):
                    if getattr(sbtn, "expanded", False):
                        for w, h in sterms:
                            w.height = 0; w.opacity = 0; w.disabled = True
                        for w, h in all_course_widgets:
                            w.height = 0; w.opacity = 0; w.disabled = True
                        sbtn.text = sbtn.text.replace("-", "+")
                        sbtn.expanded = False
                    else:
                        for w, h in sterms:
                            w.height = h; w.opacity = 1; w.disabled = False
                        # courses stay hidden unless term is expanded
                        sbtn.text = sbtn.text.replace("+", "-")
                        sbtn.expanded = True
                return toggle
            all_courses = []
            for _, _, tc in sem_term_widgets_raw:
                all_courses.extend(tc)
            sem_btn.expanded = False
            sem_btn.bind(on_press=make_sem_toggle(sem_btn, sem_term_widgets, all_courses))

    def _open(self, cid):
        for c in self.courses:
            if c.id == cid:
                self.manager.get_screen("files").set_course(self.token, self.client, c)
                self.manager.current = "files"
                return

    def _logout(self, _):
        self.token = None
        self.client = KetangpaiClient()
        self.courses.clear()
        self.manager.current = "login"


class FileListScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.token = None; self.client = None; self.course = None
        self.area = ContentArea.MATERIALS
        self.dir_stack = [("0", "资料根目录")]
        self.dir_items = []
        self.selected = set()
        self.dl_root = "/storage/emulated/0/Download/课堂派"

        root = BoxLayout(orientation="vertical", padding=dp(4), spacing=dp(4))

        hdr = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        hdr.add_widget(btn("< 返回", self._go_back, h=dp(38), sx=None))
        self.course_lbl = lbl("", sp(14), INDIGO, size_hint_x=1)
        hdr.add_widget(self.course_lbl)
        root.add_widget(hdr)

        tabs = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(4))
        for n, a in [("资料区", ContentArea.MATERIALS), ("互动课件", ContentArea.COURSEWARE)]:
            tabs.add_widget(btn(n, lambda _, ar=a: self._sw_area(ar), INDIGO if a==ContentArea.MATERIALS else None, h=dp(34)))
        root.add_widget(tabs)

        self.bread = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(2))
        root.add_widget(self.bread)

        self.scroll = ScrollView()
        self.fbox = BoxLayout(orientation="vertical", spacing=dp(2), size_hint_y=None)
        self.fbox.bind(minimum_height=self.fbox.setter("height"))
        self.scroll.add_widget(self.fbox)
        root.add_widget(self.scroll)

        self.path_f = inp("", h=dp(38))
        self.path_f.text = self.dl_root
        root.add_widget(self.path_f)

        act = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(4))
        act.add_widget(btn("下载选中", self._dl, INDIGO))
        act.add_widget(btn("全选", lambda x: self._sel_all(), h=dp(38)))
        act.add_widget(btn("取消", lambda x: self._sel_none(), h=dp(38)))
        act.add_widget(btn("刷新", lambda x: self._reload(), h=dp(38)))
        root.add_widget(act)

        self.st = lbl("", sp(11), size_hint_y=None, height=dp(18))
        root.add_widget(self.st)
        self.add_widget(root)

    def _go_back(self, _=None):
        if len(self.dir_stack) > 1:
            self.dir_stack.pop()
            self.selected.clear()
            self._reload()
        else:
            self.manager.current = "courses"

    def set_course(self, tok, cli, c):
        self.token, self.client, self.course = tok, cli, c
        self.course_lbl.text = c.name
        self.area = ContentArea.MATERIALS
        self.dir_stack = [("0", "资料根目录")]
        self.selected.clear()
        self._reload()

    def _sw_area(self, a):
        if self.area == a: return
        self.area = a
        self.dir_stack = [("0", "资料根目录")]
        self.selected.clear()
        self._reload()

    def _reload(self):
        if not self.token or not self.course: return
        self._status("加载中...")
        threading.Thread(target=self._reload_t, daemon=True).start()

    def _reload_t(self):
        try:
            items = self.client.list_dir(self.token, self.course.id, self.dir_stack[-1][0], self.area)
            Clock.schedule_once(lambda dt: self._on_items(items))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._status(f"失败: {e}"))

    @mainthread
    def _on_items(self, items):
        self.dir_items = items
        self._rebuild_bread()
        self._rebuild_items()
        self._status(f"{len(items)} 项")

    def _rebuild_bread(self):
        self.bread.clear_widgets()
        for i, (_, nm) in enumerate(self.dir_stack):
            if i > 0: self.bread.add_widget(Label(text=">", font_name=FONT(), font_size=sp(10), size_hint_x=None, width=dp(16)))
            self.bread.add_widget(lbl(nm, sp(10), INDIGO, size_hint_x=None, width=dp(30)+len(nm)*dp(7)))

    def _rebuild_items(self):
        self.fbox.clear_widgets()
        for i, item in enumerate(self.dir_items):
            row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(4), padding=(dp(4), dp(2)))
            cb = CheckBox(size_hint_x=None, width=dp(36), active=(i in self.selected))
            cb.bind(active=lambda cb, a, idx=i: self._toggle(idx, a))
            row.add_widget(cb)
            if isinstance(item, FolderItem):
                fb = Button(text=item.name, font_name=FONT(), font_size=sp(12),
                            halign="left", valign="middle", size_hint_x=1)
                fb.bind(on_press=lambda btn, it=item: self._enter(it))
                row.add_widget(fb)
            else:
                sz = self._fsize(item.filesize)
                row.add_widget(lbl(sz, sp(9), size_hint_x=None, width=dp(60)))
                row.add_widget(lbl(item.name, sp(12), size_hint_x=1))
            self.fbox.add_widget(row)

    def _toggle(self, idx, active):
        if active: self.selected.add(idx)
        else: self.selected.discard(idx)

    def _sel_all(self):
        self.selected = set(range(len(self.dir_items)))
        self._rebuild_items()

    def _sel_none(self):
        self.selected.clear()
        self._rebuild_items()

    def _enter(self, item):
        if isinstance(item, FolderItem):
            self.dir_stack.append((item.id, item.name))
            self.selected.clear()
            self._reload()

    @staticmethod
    def _fsize(sz):
        if sz <= 0: return ""
        if sz < 1024: return f"{sz}B"
        if sz < 1048576: return f"{sz/1024:.1f}KB"
        if sz < 1073741824: return f"{sz/1048576:.1f}MB"
        return f"{sz/1073741824:.2f}GB"

    def _status(self, msg):
        Clock.schedule_once(lambda dt: setattr(self.st, "text", msg))

    def _dl(self, *_):
        self.dl_root = self.path_f.text.strip() or self.dl_root
        if not self.selected: return self._status("请勾选文件")
        pf = "/".join(n for _, n in self.dir_stack[1:])
        if pf: pf += "/"
        tasks = []
        for i in sorted(self.selected):
            if i < 0 or i >= len(self.dir_items): continue
            item = self.dir_items[i]
            if isinstance(item, FileItem):
                tasks.append(DownloadTask(rel_path=pf+item.name, display_name=item.name,
                    raw_url=item.raw_url, file_id=item.id, course_id=self.course.id))
            elif isinstance(item, FolderItem):
                tasks.extend(collect_recursive_tasks(self.client, self.token, self.course.id,
                    self.area, dir_id=item.id, rel_prefix=pf+item.name+"/", walk=True))
        if not tasks: return self._status("无文件")
        self._dl_popup(tasks)

    def _dl_popup(self, tasks):
        total_count = len(tasks)
        ct = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(8))
        pbs = {}
        tl = BoxLayout(orientation="vertical", spacing=dp(2), size_hint_y=None)
        tl.bind(minimum_height=tl.setter("height"))
        for idx, t in enumerate(tasks[:20]):
            rw = BoxLayout(size_hint_y=None, height=dp(26), spacing=dp(4))
            rw.add_widget(lbl(t.display_name[:20], sp(9), size_hint_x=0.5))
            pb = ProgressBar(max=100, value=0, size_hint_x=0.5)
            rw.add_widget(pb)
            pbs[t.rel_path] = pb
            tl.add_widget(rw)
        if total_count > 20: tl.add_widget(lbl(f"...及其他 {total_count-20} 个", sp(9)))
        sv = ScrollView(size_hint_y=0.9); sv.add_widget(tl); ct.add_widget(sv)
        sl = lbl(f"下载中 0/{total_count}", sp(11), size_hint_y=None, height=dp(22))
        ct.add_widget(sl)
        popup = Popup(title="", content=ct, size_hint=(0.95, 0.75), auto_dismiss=False,
                     title_size=0)
        popup.open()

        lock = threading.Lock()
        pd = {}
        dl_results = []
        done = threading.Event()

        def prog(key, frac, cur, total, status):
            with lock:
                pd[key] = (frac, status)

        def work():
            try:
                safe = re.sub(r'[\\/:*?"<>|]', '_', self.course.name).strip()
                dl_root = os.path.join(self.dl_root, safe)
                os.makedirs(dl_root, exist_ok=True)
                mgr = DownloadManager(max_workers=3, retries=2,
                    copy_cookies_from=self.client.session,
                    slide_fallback_cookies=self.client.session,
                    auth_token=self.token)
                results = mgr.run_tasks(tasks, dl_root, progress=prog)
                dl_results.extend(results)
            finally:
                done.set()

        def poll(dt):
            with lock:
                finished = sum(1 for f, s in pd.values() if f >= 0.999 or s is not None)
                for k, (f, s) in pd.items():
                    pb = pbs.get(k)
                    if pb:
                        pb.value = int(f * 100)
                sl.text = f"下载中 {finished}/{total_count}"
            if not done.is_set():
                Clock.schedule_once(poll, 0.3)
            else:
                popup.dismiss()
                ok_count = sum(1 for r in dl_results if r.ok)
                pdf_count = sum(1 for r in dl_results if r.ok and r.path and r.path.endswith(".pdf"))
                fail_count = len(dl_results) - ok_count
                parts = [f"完成: {ok_count}/{total_count} 成功"]
                if pdf_count: parts.append(f"({pdf_count} 个转PDF)")
                if fail_count: parts.append(f"{fail_count} 个失败")
                self._status(" ".join(parts))

        threading.Thread(target=work, daemon=True).start()
        Clock.schedule_once(poll, 0.3)


class KtpApp(App):
    def build(self):
        try:
            # Ensure font is loaded before UI
            _find_font()
            sm = ScreenManager(transition=SlideTransition())
            sm.add_widget(LoginScreen(name="login"))
            sm.add_widget(CourseListScreen(name="courses"))
            sm.add_widget(FileListScreen(name="files"))
            return sm
        except Exception:
            # 将崩溃信息写入文件，方便排查
            crash_log = os.path.join(
                os.getenv("EXTERNAL_STORAGE", "/storage/emulated/0"),
                "ktp_crash.log",
            )
            try:
                with open(crash_log, "w", encoding="utf-8") as f:
                    f.write(traceback.format_exc())
            except Exception:
                pass
            # 显示错误界面而不是直接闪退
            from kivy.uix.label import Label
            from kivy.core.text import LabelBase
            err_msg = traceback.format_exc()
            return Label(
                text=f"启动失败:\n{err_msg[:500]}",
                font_size=sp(10),
                halign="left",
                valign="top",
                text_size=(dp(350), None),
            )


if __name__ == "__main__":
    try:
        KtpApp().run()
    except Exception:
        crash_log = "/storage/emulated/0/ktp_crash.log"
        try:
            with open(crash_log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
