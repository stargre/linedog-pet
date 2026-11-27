# -*- coding: utf-8 -*-
"""对话 Agent：把"人设 + 桌面感知 + 长期记忆 + 工具"织成一次多轮推理。

一次 run() 的流程（事件驱动，不阻塞 UI）：

    组装 messages（system: 人设+环境+记忆）
        └─ LLM（流式，带 tools）
             ├─ 模型只回了文字 -> 结束，交付气泡/对话框
             └─ 模型要调工具 -> 本地执行 -> 把结果作为 role=tool 追加
                                -> 再请求一轮（最多 agent_max_rounds 轮）
"""
import json
import time

from PyQt6.QtCore import QObject

from . import ai as ai_mod
from .llm import LLMClient
from .tools import build_default_registry

MAX_HISTORY = 10

# 系统提示里始终强调的"行为准则"：短、口语、先动手再说话
RULES = (
    "【行为准则】\n"
    "1. 你能调用工具直接操作小狗。想让它动、变大、睡觉、玩、记事情，就调用工具，"
    "不要只说\"我会…\"。\n"
    "2. 每次回复不超过 30 个字，口语化、可爱，不要 markdown、不要分点列表。\n"
    "3. 需要用户信息时先查记忆（recall）；用户告诉你值得长期记住的事，就调用 remember。\n"
    "4. 不知道就直说，不要编。用户明显在忙（写代码/全屏/离开）时，话要更短。\n"
    "5. 一次最多调用 2 个工具，别把简单事拆成一堆调用。"
)


class Agent(QObject):
    """有状态但单次运行互斥：同一时刻只处理一轮对话。"""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.cfg = app.cfg
        self.llm = LLMClient(self.cfg)
        self.tools = build_default_registry(app)
        self._run = None

    # ---------------- 对外 ----------------
    def available(self):
        return self.llm.available()

    def tool_names(self):
        return self.tools.names()

    def run(self, user_text, history=None, on_delta=None, on_note=None,
            on_done=None, on_error=None):
        """发起一轮对话。回调全部在 GUI 线程。"""
        if not self.available():
            if on_error:
                on_error("AI 还没配置：设置 → AI 填入 API 地址和密钥")
            return None
        if self._run is not None:
            if on_error:
                on_error("上一句还在想呢，等我说完~")
            return None

        self._run = {
            "user": user_text,
            "messages": self._build_messages(user_text, history or []),
            "rounds": 0,
            "text": "",
            "on_delta": on_delta,
            "on_note": on_note,
            "on_done": on_done,
            "on_error": on_error,
            "started": time.time(),
        }
        self._request()
        return self._run

    def cancel(self):
        self._run = None
        try:
            self.llm.cancel_all()
        except Exception:
            pass

    # ---------------- 请求 ----------------
    def _request(self):
        run = self._run
        if run is None:
            return
        tools = self.tools.specs() if self.cfg.get("agent_enabled", True) else None
        self.llm.stream_chat(
            run["messages"],
            tools=tools,
            on_delta=self._on_delta,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _on_delta(self, piece):
        run = self._run
        if run is None or not piece:
            return
        run["text"] += piece
        cb = run.get("on_delta")
        if cb:
            try:
                cb(piece)
            except Exception:
                pass

    def _on_done(self, text, calls):
        run = self._run
        if run is None:
            return
        if text:
            run["text"] = run["text"] if len(run["text"]) >= len(text) else text

        calls = calls or []
        max_rounds = int(self.cfg.get("agent_max_rounds", 4) or 4)
        if calls and self.cfg.get("agent_enabled", True) and run["rounds"] < max_rounds:
            run["rounds"] += 1
            self._apply_tool_round(run, text, calls)
            self._request()
            return

        final = (run["text"] or text or "").strip()
        self._finish(final, None)

    def _on_error(self, msg):
        run = self._run
        if run is None:
            return
        self._finish("", msg)

    def _finish(self, text, error):
        run = self._run
        self._run = None
        if run is None:
            return
        if error:
            cb = run.get("on_error")
            if cb:
                try:
                    cb(error)
                except Exception:
                    pass
            return
        cb = run.get("on_done")
        if cb:
            try:
                cb(text)
            except Exception:
                pass

    # ---------------- 工具回合 ----------------
    def _apply_tool_round(self, run, text, calls):
        assistant = {"role": "assistant", "content": text or None,
                     "tool_calls": [
                         {"id": c["id"], "type": "function",
                          "function": {"name": c["name"],
                                       "arguments": json.dumps(c["arguments"],
                                                               ensure_ascii=False)}}
                         for c in calls]}
        run["messages"].append(assistant)

        for call in calls:
            name = call.get("name")
            args = call.get("arguments") or {}
            ok, payload = self.tools.call(name, args)
            note = self._describe(name, args, ok, payload)
            if note and run.get("on_note"):
                try:
                    run["on_note"](note)
                except Exception:
                    pass
            run["messages"].append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "content": json.dumps(payload, ensure_ascii=False)[:1200],
            })
        # 中间轮的旁白不要混进最终正文
        run["text"] = ""

    @staticmethod
    def _describe(name, args, ok, payload):
        """给对话框显示的一行"动作说明"（人话）。"""
        if not ok:
            return "· %s 没成功：%s" % (name, payload.get("error", ""))
        table = {
            "say": lambda: "说了一句",
            "move": lambda: "走到了%s" % args.get("place", ""),
            "set_size": lambda: "变成 %s 像素" % payload.get("size", ""),
            "sleep": lambda: "去睡觉了",
            "wake": lambda: "醒了",
            "play": lambda: "开始玩%s" % args.get("kind", ""),
            "feed": lambda: "吃上了",
            "set_visible": lambda: "显示" if args.get("visible") else "隐藏",
            "get_status": lambda: "看了看自己的状态",
            "get_environment": lambda: "看了看你的桌面",
            "remember": lambda: "记住了「%s」" % args.get("key", ""),
            "recall": lambda: "回忆了一下",
            "open_panel": lambda: "打开了%s" % args.get("panel", ""),
        }
        fn = table.get(name)
        return "· " + (fn() if fn else name)

    # ---------------- Prompt 组装 ----------------
    def _build_messages(self, user_text, history):
        msgs = [{"role": "system", "content": self._system_prompt(user_text)}]
        for item in (history or [])[-MAX_HISTORY:]:
            role = item.get("role")
            content = item.get("content")
            if role in ("user", "assistant") and content:
                msgs.append({"role": role, "content": str(content)[:800]})
        msgs.append({"role": "user", "content": str(user_text)[:1000]})
        return msgs

    def _system_prompt(self, user_text):
        cfg = self.cfg
        parts = [ai_mod.persona_for(cfg, cfg.get("character"))]

        # 环境感知
        if cfg.get("ai_context", True) and getattr(self.app, "perception", None):
            try:
                env = self.app.perception.context_text(max_events=4)
                if env:
                    parts.append("【当前环境（本地感知，仅供理解场景，不要逐条复述）】\n" + env)
            except Exception:
                pass

        # 长期记忆
        if cfg.get("memory_enabled", True) and getattr(self.app, "memory", None):
            try:
                mem = self.app.memory.context_text(budget=700, query=user_text)
                if mem:
                    parts.append("【长期记忆】\n" + mem)
            except Exception:
                pass

        if cfg.get("agent_enabled", True):
            parts.append("【可用工具】" + "、".join(self.tools.names()))
        parts.append(RULES)
        return "\n\n".join(p for p in parts if p)
