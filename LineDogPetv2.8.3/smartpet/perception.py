# -*- coding: utf-8 -*-
"""桌面环境感知层。

把原先散落在 PetApp._tick_sense / ObserveManager 里的"取数"逻辑收拢成一个
统一、结构化的感知源，供 AI Agent 使用：

    PerceptionBus  低频采样（默认 3s）前台窗口 / 全屏 / 闲置 / CPU / 音乐，
                   把"变化"提炼成事件（切应用、进出全屏、离开、久坐写码…）
    PerceptionSnapshot  某一时刻的完整快照，可序列化、可渲染成 prompt 文本

设计约束（沿用本体性能思路）：
- 只用 winapi 里已有的轻量接口，3 秒一次，不引入额外线程；
- 采样失败（非 Windows、API 异常）一律降级为 None，绝不抛给 UI；
- 事件只保留最近 N 条（deque），不会无限增长。
"""
import time
from collections import deque
from dataclasses import dataclass, field, asdict

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import winapi

# ---------------- 应用分类 ----------------
# 顺序有意义：先匹配更具体的类别
CATEGORY_RULES = [
    ("code", ["VISUAL STUDIO CODE", "VS CODE", "VSCODE", "PYCHARM", "INTELLIJ", "IDEA",
              "WEBSTORM", "CURSOR", "WINDSURF", "SUBLIME", "NOTEPAD++",
              "GOLAND", "CLION", "RIDERS", "PHPSTORM", "RUSTROVER",
              "DATAGRIP", "JETBRAINS", "ANDROID STUDIO", "ECLIPSE", "DEV-C++",
              "TRAE", "TERMINAL", "POWERSHELL", "WINDOWS TERMINAL", "CMD.EXE"]),
    ("game", ["STEAM", "EPIC GAMES", "ORIGIN", "UBISOFT", "BATTLE.NET",
              "MINECRAFT", "LEAGUE OF LEGENDS", "VALORANT", "CSGO", "CS2",
              "原神", "STARDEW", "ELDEN RING", "绝地求生", "永劫无间"]),
    ("office", ["WORD", "EXCEL", "POWERPOINT", "WPS", "ONENOTE", "OUTLOOK",
                "ACROBAT", "FOXIT", "PDF"]),
    ("media", ["BILIBILI", "哔哩哔哩", "YOUTUBE", "POTPLAYER", "VLC", "MPV",
               "NETFLIX", "IQIYI", "爱奇艺", "腾讯视频", "优酷"]),
    ("chat", ["WECHAT", "微信", "QQ", "TELEGRAM", "DISCORD", "SLACK",
              "DINGTALK", "钉钉", "FEISHU", "飞书", "WHATSAPP"]),
    ("browser", ["CHROME", "EDGE", "FIREFOX", "SAFARI", "OPERA", "BRAVE",
                 "360SE", "QQBROWSER", "搜狗", "浏览器"]),
]

CATEGORY_CN = {
    "code": "写代码", "game": "玩游戏", "office": "办公文档",
    "media": "看视频", "chat": "聊天", "browser": "浏览网页",
    "music": "听歌", "other": "其他", "": "未知",
}

BROWSER_CLASSES = ("Chrome_WidgetWin", "MozillaWindowClass", "OperaWindowClass",
                   "IEFrame", "360se6_Frame", "msedge ")

# 事件去抖：同一个 kind 在这个窗口内只记一次
EVENT_COOLDOWN = {
    "app_switch": 15.0,
    "fullscreen": 30.0,
    "idle_away": 300.0,
    "idle_back": 60.0,
    "deep_idle": 1800.0,
}


def classify(title, cls=""):
    """窗口标题 / 类名 -> 应用类别（code/game/office/media/chat/browser/other）。"""
    t = (title or "").upper()
    c = cls or ""
    if t:
        for cat, keys in CATEGORY_RULES:
            if any(k in t for k in keys):
                return cat
    # 标题没线索时靠类名兜底（Chromium / Gecko / Electron 壳）
    if any(k in c for k in BROWSER_CLASSES):
        return "browser"
    return "other"


def _short(text, n=40):
    s = (text or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


@dataclass
class PerceptionSnapshot:
    """某一时刻的桌面环境快照。字段全部可安全为空。"""
    ts: float = 0.0
    hour: int = 0
    weekday: int = 0
    idle_sec: float = 0.0
    app_title: str = ""
    app_class: str = ""
    app_category: str = ""
    fullscreen: bool = False
    cpu: float = None
    song: str = ""
    pet_state: str = ""
    pet_mood: float = 0.0
    pet_energy: float = 0.0
    code_minutes: int = 0
    events: list = field(default_factory=list)   # [(hms, kind, text)]

    # ---- 渲染 ----
    def time_bucket(self):
        h = self.hour
        if 5 <= h < 9:
            return "清晨"
        if 9 <= h < 12:
            return "上午"
        if 12 <= h < 14:
            return "中午"
        if 14 <= h < 18:
            return "下午"
        if 18 <= h < 22:
            return "傍晚"
        if 22 <= h < 24:
            return "深夜"
        return "凌晨"

    def user_activity(self):
        """从闲置时长推断用户当前在做什么。"""
        if self.idle_sec >= 600:
            return "离开很久了"
        if self.idle_sec >= 180:
            return "离开了一会儿"
        if self.idle_sec >= 60:
            return "停手发呆"
        return "正在用电脑"

    def app_text(self):
        if not self.app_title:
            return "未知窗口"
        cat = CATEGORY_CN.get(self.app_category, "其他")
        return "%s（%s）" % (_short(self.app_title, 46), cat)

    def context_text(self, max_events=4):
        """渲染成给 LLM 的一段紧凑中文描述（不要逐条罗列，供模型理解场景）。"""
        clock = time.strftime("%H:%M", time.localtime(self.ts or time.time()))
        parts = ["现在 %s（%s）" % (clock, self.time_bucket()),
                 "前台：%s" % self.app_text(),
                 "用户：%s" % self.user_activity()]
        if self.fullscreen:
            parts.append("处于全屏应用")
        if self.code_minutes >= 10:
            parts.append("已连续写代码约 %d 分钟" % self.code_minutes)
        if self.cpu is not None:
            parts.append("CPU %.0f%%" % self.cpu)
        if self.song:
            parts.append("正在听《%s》" % _short(self.song, 30))
        if self.pet_state:
            parts.append("小狗状态：%s（心情 %d/体力 %d）" % (
                self.pet_state, int(self.pet_mood), int(self.pet_energy)))
        text = "；".join(parts)
        evs = self.events[-max_events:] if self.events else []
        if evs:
            text += "\n最近发生：" + "；".join("%s %s" % (hms, t) for hms, _k, t in evs)
        return text

    def to_dict(self):
        return asdict(self)


class PerceptionBus(QObject):
    """低频采样 + 事件提炼。emit snapshot_changed / event_occurred。"""

    snapshot_changed = pyqtSignal(object)          # PerceptionSnapshot
    event_occurred = pyqtSignal(str, str)          # (kind, text)

    MAX_EVENTS = 24

    def __init__(self, cfg, interval_ms=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self._events = deque(maxlen=self.MAX_EVENTS)
        self._snap = PerceptionSnapshot()
        self._timer = QTimer(self)
        self._timer.setInterval(int(interval_ms or self.cfg.get("perception_interval", 3000)))
        self._timer.timeout.connect(self._tick)
        self._started = False

        # 变化检测状态
        self._last_sig = ""
        self._last_fs = None
        self._last_idle_level = 0
        self._last_kind = ""
        self._music = ""
        self._clipboard_note = ""
        self._code_since = None
        self._event_at = {}
        self._pet_provider = None

    # ---------------- 生命周期 ----------------
    def set_pet_provider(self, fn):
        """注入 pet 状态读取函数 () -> (state, mood, energy)。"""
        self._pet_provider = fn

    def start(self):
        if not self._started and self.cfg.get("perception_enabled", True):
            self._started = True
            self._timer.start()
            self._tick()

    def stop(self):
        self._started = False
        self._timer.stop()

    def set_enabled(self, on):
        self.start() if on else self.stop()

    # ---------------- 外部事件注入 ----------------
    def set_music(self, song):
        song = (song or "").strip()
        if song and song != self._music:
            self._music = song
            self._push("music_change", "开始听《%s》" % _short(song, 28))

    def note_clipboard(self, text):
        t = _short(text, 60)
        if t and t != self._clipboard_note:
            self._clipboard_note = t
            self._push("clipboard", "复制了：%s" % t)

    def note(self, kind, text):
        """给业务层用：把一次互动记进感知流。"""
        self._push(kind, text)

    # ---------------- 采样 ----------------
    def snapshot(self):
        return self._snap

    def recent_events(self, n=6):
        return list(self._events)[-n:]

    def context_text(self, max_events=4):
        return self._snap.context_text(max_events=max_events)

    def _push(self, kind, text):
        now = time.time()
        cd = EVENT_COOLDOWN.get(kind, 2.0)
        if now - self._event_at.get(kind, 0) < cd:
            return
        self._event_at[kind] = now
        hms = time.strftime("%H:%M", time.localtime(now))
        self._events.append((hms, kind, text))
        try:
            self.event_occurred.emit(kind, text)
        except Exception:
            pass

    def _tick(self):
        snap = PerceptionSnapshot(ts=time.time())
        lt = time.localtime(snap.ts)
        snap.hour, snap.weekday = lt.tm_hour, lt.tm_wday

        # 前台窗口
        try:
            fg = winapi.foreground_info() if winapi.IS_WINDOWS else None
        except Exception:
            fg = None
        if fg:
            snap.app_title = fg.get("title") or ""
            snap.app_class = fg.get("cls") or ""
            snap.fullscreen = bool(fg.get("fullscreen"))

        # 类别兜底：媒体播放器优先视为听歌
        cat = classify(snap.app_title, snap.app_class)
        if self._music and cat == "media":
            cat = "media"
        snap.app_category = cat

        # 闲置 / CPU
        try:
            snap.idle_sec = float(winapi.get_idle_seconds() or 0)
        except Exception:
            snap.idle_sec = 0.0
        try:
            snap.cpu = winapi.cpu_percent()
        except Exception:
            snap.cpu = None

        # 音乐 / 宠物状态
        snap.song = self._music
        if self._pet_provider:
            try:
                st, mood, energy = self._pet_provider()
                snap.pet_state, snap.pet_mood, snap.pet_energy = st, mood, energy
            except Exception:
                pass

        # 连续写代码时长
        if cat == "code" and snap.idle_sec < 120:
            if self._code_since is None:
                self._code_since = snap.ts
            snap.code_minutes = int((snap.ts - self._code_since) / 60)
        else:
            self._code_since = None
            snap.code_minutes = 0

        # ---------------- 变化 -> 事件 ----------------
        sig = (snap.app_class, _short(snap.app_title, 60))
        if self._last_sig and sig != self._last_sig:
            if cat != self._last_kind:
                self._push("app_switch", "切到了%s" % CATEGORY_CN.get(cat, "其他"))
            else:
                self._push("app_switch", "换了窗口：%s" % _short(snap.app_title, 26))
        self._last_sig = sig
        self._last_kind = cat

        if self._last_fs is not None and snap.fullscreen != self._last_fs:
            self._push("fullscreen", "进入全屏应用" if snap.fullscreen else "退出全屏")
        self._last_fs = snap.fullscreen

        level = 2 if snap.idle_sec >= 600 else (1 if snap.idle_sec >= 180 else 0)
        if level > self._last_idle_level:
            self._push("deep_idle" if level == 2 else "idle_away",
                       "用户离开很久了" if level == 2 else "用户离开了一会儿")
        elif level < self._last_idle_level:
            self._push("idle_back", "用户回来了")
        self._last_idle_level = level

        snap.events = list(self._events)[-6:]
        self._snap = snap
        try:
            self.snapshot_changed.emit(snap)
        except Exception:
            pass
