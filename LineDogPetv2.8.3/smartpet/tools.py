# -*- coding: utf-8 -*-
"""工具注册表：把桌宠的能力包装成 LLM 可调用的 function。

一个工具 = 名字 + 中文描述 + JSON Schema + 处理函数。
Agent 只认这层接口，不直接摸 PetWidget / PetApp 内部，方便以后加新能力。

安全约定：
- 所有工具都是"本地、可逆、低风险"的（说话/走动/变大变小/睡觉/玩/记忆/看状态）；
- 不提供执行任意命令、读写任意文件、模拟键鼠的能力；
- 处理函数必须返回可 JSON 序列化的 dict，异常由注册表兜住并回给模型。
"""
import time

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QCursor


class Tool:
    def __init__(self, name, description, parameters, handler, permission="safe"):
        self.name = name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.handler = handler
        self.permission = permission

    def spec(self):
        return {"type": "function",
                "function": {"name": self.name,
                             "description": self.description,
                             "parameters": self.parameters}}


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def add(self, tool):
        self._tools[tool.name] = tool
        return tool

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return sorted(self._tools)

    def specs(self):
        return [t.spec() for t in self._tools.values()]

    def call(self, name, args=None):
        """执行工具。返回 (ok: bool, payload: dict)。"""
        tool = self._tools.get((name or "").strip())
        if tool is None:
            return False, {"error": "没有这个工具：%s" % name,
                           "available": self.names()}
        args = args if isinstance(args, dict) else {}
        if "_raw" in args:
            return False, {"error": "参数不是合法 JSON，请重新组织参数"}
        try:
            result = tool.handler(**args)
        except TypeError as e:
            return False, {"error": "参数不匹配：%s" % e}
        except Exception as e:
            return False, {"error": "%s: %s" % (type(e).__name__, e)}
        if result is None:
            result = {"ok": True}
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("ok", True)
        return True, result


# ---------------- 内置工具 ----------------
def _screen_point(app, place):
    """把语义位置换算成屏幕坐标（返回宠物中心点）。"""
    pet = app.pet
    scr = (QApplication.screenAt(pet.frameGeometry().center())
           or QApplication.primaryScreen())
    g = scr.availableGeometry()
    w, h = pet.width(), pet.height()
    place = (place or "center").lower()

    if place == "near_cursor":
        c = QCursor.pos()
        return c.x(), c.y()
    margin = 24
    table = {
        "center": (g.left() + g.width() // 2, g.top() + g.height() // 2),
        "left": (g.left() + w // 2 + margin, g.top() + g.height() // 2),
        "right": (g.right() - w // 2 - margin, g.top() + g.height() // 2),
        "top": (g.left() + g.width() // 2, g.top() + h // 2 + margin),
        "bottom": (g.left() + g.width() // 2, g.bottom() - h // 2 - margin),
        "corner": (g.right() - w // 2 - 8, g.bottom() - h // 2 - 8),
    }
    return table.get(place, table["center"])


def _tool_say(app):
    def handler(text=""):
        text = str(text or "").strip()[:200]
        if not text:
            return {"ok": False, "error": "没有要说的内容"}
        app.pet.show_bubble(text, 5000)
        return {"ok": True, "said": text}
    return Tool(
        "say", "在桌面上让小狗冒出一句话气泡（最直接的对用户输出方式）。",
        {"type": "object",
         "properties": {"text": {"type": "string", "description": "要说的话，30 字以内"}},
         "required": ["text"]},
        handler)


def _tool_move(app):
    def handler(place="center"):
        cx, cy = _screen_point(app, place)
        app.pet.wake()
        ok = app.pet.relocate(cx, cy, speed=6)
        return {"ok": bool(ok), "moved_to": place}
    return Tool(
        "move", "让小狗走到屏幕上的某个位置。place 可选：center/left/right/top/bottom/corner/near_cursor。",
        {"type": "object",
         "properties": {"place": {"type": "string",
                                  "enum": ["center", "left", "right", "top",
                                           "bottom", "corner", "near_cursor"]}},
         "required": ["place"]},
        handler)


def _tool_set_size(app):
    def handler(size=150):
        try:
            size = int(size)
        except Exception:
            size = 150
        size = max(80, min(260, size))
        app.cfg["size"] = size
        app.pet.replay()
        app.save()
        return {"ok": True, "size": size}
    return Tool(
        "set_size", "调整小狗大小（80~260 像素）。",
        {"type": "object",
         "properties": {"size": {"type": "integer", "minimum": 80, "maximum": 260}},
         "required": ["size"]},
        handler)


def _tool_sleep(app):
    def handler():
        app.pet.go_sleep()
        return {"ok": True}
    return Tool("sleep", "让小狗去睡觉（趴下/蜷起来）。",
                {"type": "object", "properties": {}}, handler)


def _tool_wake(app):
    def handler():
        app.pet.wake()
        return {"ok": True}
    return Tool("wake", "叫醒小狗（如果它在睡觉）。",
                {"type": "object", "properties": {}}, handler)


def _tool_play(app):
    def handler(kind="play"):
        kind = (kind or "play").lower()
        if kind == "chase":
            app.start_chase()
        elif kind == "hide":
            app.start_hide()
        else:
            app.play()
        return {"ok": True, "kind": kind}
    return Tool(
        "play", "陪小狗玩。kind：chase=追球，hide=躲猫猫，play=普通玩耍。",
        {"type": "object",
         "properties": {"kind": {"type": "string", "enum": ["chase", "hide", "play"]}},
         "required": ["kind"]},
        handler)


def _tool_feed(app):
    def handler():
        app.feed()
        return {"ok": True, "energy": round(float(app.cfg.get("energy", 0)), 1)}
    return Tool("feed", "喂小狗吃东西（恢复体力、小幅提升心情）。",
                {"type": "object", "properties": {}}, handler)


def _tool_visible(app):
    def handler(visible=True):
        vis = bool(visible)
        app.set_pet_visible(vis)
        return {"ok": True, "visible": vis}
    return Tool(
        "set_visible", "显示或隐藏小狗。visible=true 显示，false 隐藏（快捷键 Ctrl+Alt+D 同效）。",
        {"type": "object",
         "properties": {"visible": {"type": "boolean"}},
         "required": ["visible"]},
        handler)


def _tool_status(app):
    def handler():
        return {"ok": True,
                "mood": round(float(app.cfg.get("mood", 0)), 1),
                "energy": round(float(app.cfg.get("energy", 0)), 1),
                "state": app.pet.state,
                "size": app.cfg.get("size"),
                "character": app.cfg.get("character"),
                "time": time.strftime("%H:%M")}
    return Tool("get_status", "读取小狗当前的心情、体力、状态、大小和当前时间。",
                {"type": "object", "properties": {}}, handler)


def _tool_environment(app):
    def handler():
        snap = getattr(app, "perception", None)
        if snap is None:
            return {"ok": False, "error": "感知模块未启用"}
        return {"ok": True, "environment": snap.context_text(max_events=6),
                "foreground": snap.snapshot().app_title,
                "category": snap.snapshot().app_category,
                "idle_sec": int(snap.snapshot().idle_sec),
                "fullscreen": snap.snapshot().fullscreen}
    return Tool(
        "get_environment", "重新读一次当前桌面环境（前台窗口、用户是否离开、全屏、CPU 等）。",
        {"type": "object", "properties": {}}, handler)


def _tool_remember(app):
    def handler(key="", value="", kind="fact"):
        if not (key and value):
            return {"ok": False, "error": "key 和 value 都不能为空"}
        ok = app.memory.remember(str(key)[:80], str(value)[:400],
                                 kind=str(kind or "fact")[:16], source="ai")
        return {"ok": ok, "key": key}
    return Tool(
        "remember",
        "把关于用户的一条长期信息记下来（跨会话保留）。key 用简短名词，如 名字/职业/喜好/项目，value 是具体内容。",
        {"type": "object",
         "properties": {"key": {"type": "string", "description": "简短键名，如 名字、职业、正在做的项目"},
                        "value": {"type": "string", "description": "具体内容，尽量精炼"}},
         "required": ["key", "value"]},
        handler)


def _tool_recall(app):
    def handler(query=""):
        q = str(query or "").strip()
        if not q:
            facts = app.memory.facts(limit=10)
            return {"ok": True, "facts": [{"key": f["key"], "value": f["value"]}
                                          for f in facts]}
        hit = app.memory.search(q, limit=5)
        return {"ok": True,
                "facts": [{"key": r["key"], "value": r["value"]} for r in hit["facts"]],
                "episodes": [{"when": time.strftime("%m-%d %H:%M",
                                                    time.localtime(r["ts"])),
                              "text": r["summary"]} for r in hit["episodes"]]}
    return Tool(
        "recall", "检索长期记忆里和关键词相关的事实与经历。",
        {"type": "object",
         "properties": {"query": {"type": "string", "description": "要回忆的关键词"}},
         "required": ["query"]},
        handler)


def _tool_open_panel(app):
    def handler(panel="status"):
        panel = (panel or "status").lower()
        if panel == "diary":
            app.open_diary()
        elif panel == "settings":
            app.open_settings("")
        elif panel == "chat":
            app.open_chat()
        else:
            app.open_status()
        return {"ok": True, "panel": panel}
    return Tool(
        "open_panel", "打开一个界面面板。panel：status=状态面板，diary=日记，settings=设置，chat=对话窗。",
        {"type": "object",
         "properties": {"panel": {"type": "string",
                                  "enum": ["status", "diary", "settings", "chat"]}},
         "required": ["panel"]},
        handler)


def build_default_registry(app):
    """按当前配置装配工具集。"""
    reg = ToolRegistry()
    for factory in (_tool_say, _tool_move, _tool_set_size, _tool_sleep, _tool_wake,
                    _tool_play, _tool_feed, _tool_visible, _tool_status,
                    _tool_environment, _tool_open_panel):
        reg.add(factory(app))
    if app.cfg.get("memory_enabled", True):
        reg.add(_tool_remember(app))
        reg.add(_tool_recall(app))
    return reg
