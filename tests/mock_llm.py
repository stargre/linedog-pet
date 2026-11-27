# -*- coding: utf-8 -*-
"""极简 OpenAI 兼容 mock 服务：用于验证 Agent 的工具调用与流式解析。

支持两种剧本：
    tool_then_text   第一次请求返回 tool_call（remember），带 tool 结果后返回文字
    text_only        直接返回文字
请求体全部记录在 server.requests 里，便于断言"工具定义是否真的发出去了"。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _sse(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def _tool_chunks(name="remember", args_fragments=None):
    args_fragments = args_fragments or ['{"key": "名字", "value": "小张"}']
    out = [_sse({"choices": [{"delta": {"role": "assistant", "tool_calls": [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": name, "arguments": ""}}]}}]})]
    for frag in args_fragments:
        out.append(_sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": frag}}]}}]}))
    return out


def _text_chunks(text, size=3):
    return [_sse({"choices": [{"delta": {"content": text[i:i + size]}}]})
            for i in range(0, len(text), size)]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        srv = self.server.mock
        srv.requests.append(payload)

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for chunk in srv.responder(payload):
                self.wfile.write(chunk)
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            body = json.dumps(srv.json_responder(payload), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class MockLLM:
    def __init__(self, mode="tool_then_text", final_text="记住啦，小张！"):
        self.mode = mode
        self.final_text = final_text
        self.requests = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.mock = self
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = None

    # ---- 剧本 ----
    def has_tool_result(self, payload):
        return any(m.get("role") == "tool" for m in (payload.get("messages") or []))

    def responder(self, payload):
        if self.mode == "tool_then_text" and not self.has_tool_result(payload):
            return _tool_chunks()
        return _text_chunks(self.final_text)

    def json_responder(self, payload):
        return {"choices": [{"message": {"role": "assistant",
                                         "content": self.final_text}}]}

    # ---- 生命周期 ----
    @property
    def base_url(self):
        return "http://127.0.0.1:%d/v1" % self.port

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
