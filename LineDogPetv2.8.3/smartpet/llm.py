# -*- coding: utf-8 -*-
"""OpenAI 兼容的流式 LLM 客户端（含 function calling）。

只做一件事：把 messages（可带 tools）发到用户自己配置的 /chat/completions，
边收边回调。不做重试风暴、不做代理、不落盘。

对外：
    client.available()                      是否配置了 base + key
    client.stream_chat(messages, tools, ...) 发起一次请求，返回 QThread

回调都在 GUI 线程触发（Qt 信号跨线程投递），可以直接改 UI。
"""
import json

from PyQt6.QtCore import QThread, pyqtSignal


def _chat_url(base):
    base = (base or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _short(s, n=160):
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


class LLMWorker(QThread):
    """一次请求。流式时边收边 emit delta；结束时给出完整文本与工具调用。"""

    delta = pyqtSignal(str)
    done = pyqtSignal(str, object)      # (text, tool_calls: list[dict])
    failed = pyqtSignal(str)

    def __init__(self, cfg, messages, tools=None, stream=True, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.messages = messages
        self.tools = tools or []
        self.stream = bool(stream) and bool(cfg.get("agent_stream", True))

    # ---------------- 主流程 ----------------
    def run(self):
        try:
            import requests
        except Exception:
            self.failed.emit("缺少 requests 库，请先 pip install requests")
            return

        cfg = self.cfg
        url = _chat_url(cfg.get("ai_base"))
        key = (cfg.get("ai_key") or "").strip()
        if not url or not key:
            self.failed.emit("还没填 API 地址或密钥哦（托盘右键 → 设置 → AI）")
            return

        payload = {
            "model": cfg.get("ai_model") or "deepseek-chat",
            "messages": self.messages,
            "temperature": float(cfg.get("ai_temperature", 0.7)),
            "max_tokens": int(cfg.get("ai_max_tokens", 400)),
            "stream": self.stream,
        }
        if self.tools:
            payload["tools"] = self.tools
            payload["tool_choice"] = "auto"

        headers = {"Authorization": "Bearer " + key,
                   "Content-Type": "application/json"}

        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 stream=self.stream, timeout=(10, 120))
        except Exception as e:
            self.failed.emit("连不上 API：%s" % _short(str(e), 80))
            return

        if resp.status_code != 200:
            body = ""
            try:
                body = resp.text
            except Exception:
                pass
            self.failed.emit("API 返回错误 %s：%s"
                             % (resp.status_code, _short(body, 120)))
            return

        try:
            if self.stream:
                text, calls = self._read_stream(resp)
            else:
                text, calls = self._read_plain(resp)
        except Exception as e:
            self.failed.emit("解析回复失败：%s" % _short(str(e), 80))
            return
        finally:
            try:
                resp.close()
            except Exception:
                pass

        self.done.emit(text, calls)

    # ---------------- 流式解析 ----------------
    def _read_stream(self, resp):
        text_parts = []
        slots = {}
        for raw in resp.iter_lines(decode_unicode=False):
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", "ignore").strip()
            except Exception:
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    break
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get("delta") or {}

            content = delta.get("content")
            if content:
                text_parts.append(content)
                try:
                    self.delta.emit(content)
                except Exception:
                    pass

            for tc in (delta.get("tool_calls") or []):
                idx = int(tc.get("index", 0) or 0)
                slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
        return "".join(text_parts).strip(), self._finalize_calls(slots)

    # ---------------- 非流式 ----------------
    def _read_plain(self, resp):
        data = resp.json()
        choice = (data.get("choices") or [{}])[0] or {}
        msg = choice.get("message") or {}
        text = (msg.get("content") or "").strip()
        slots = {}
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            slots[i] = {"id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        "arguments": fn.get("arguments") or ""}
        return text, self._finalize_calls(slots)

    # ---------------- 工具调用归一化 ----------------
    @staticmethod
    def _finalize_calls(slots):
        out = []
        for idx in sorted(slots):
            slot = slots[idx]
            name = (slot.get("name") or "").strip()
            if not name:
                continue
            raw = slot.get("arguments") or "{}"
            args = {}
            if isinstance(raw, dict):
                args = raw
            else:
                try:
                    args = json.loads(raw) if raw.strip() else {}
                except Exception:
                    args = {"_raw": raw}
            out.append({"id": slot.get("id") or "call_%d" % idx,
                        "name": name,
                        "arguments": args})
        return out


class LLMClient:
    """薄封装：负责拼接 URL 头、托管 worker 引用。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._workers = []

    def available(self):
        return bool((self.cfg.get("ai_base") or "").strip()
                    and (self.cfg.get("ai_key") or "").strip())

    def stream_chat(self, messages, tools=None, on_delta=None,
                    on_done=None, on_error=None, stream=None):
        if stream is None:
            stream = bool(self.cfg.get("agent_stream", True))
        w = LLMWorker(self.cfg, messages, tools, stream=stream)
        self._workers.append(w)
        if on_delta is not None:
            w.delta.connect(on_delta)
        if on_done is not None:
            w.done.connect(on_done)
        if on_error is not None:
            w.failed.connect(on_error)

        def _cleanup(*_args):
            try:
                self._workers.remove(w)
            except ValueError:
                pass
            w.deleteLater()

        w.finished.connect(_cleanup)
        w.start()
        return w

    def cancel_all(self):
        for w in list(self._workers):
            try:
                w.requestInterruption()
            except Exception:
                pass
