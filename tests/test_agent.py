# -*- coding: utf-8 -*-
"""Agent 集成测试：mock LLM -> 工具调用 -> 记忆落库 -> 最终回复。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ctx

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from mock_llm import MockLLM
from smartpet.agent import Agent


def _wait(loop, ms=15000):
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(ms)
    loop.exec()
    timer.stop()


class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ldp_agent_")
        self.server = MockLLM().start()
        self.app = _ctx.make_app_stub(self.tmp)
        self.app.cfg.update({
            "ai_base": self.server.base_url,
            "ai_key": "test-key",
            "ai_model": "mock-model",
            "ai_enabled": True,
        })

    def tearDown(self):
        self.server.stop()
        try:
            self.app.memory.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, agent, text, history=None):
        state = {"deltas": [], "notes": [], "done": None, "error": None}
        loop = QEventLoop()

        def on_done(t):
            state["done"] = t
            loop.quit()

        def on_error(e):
            state["error"] = e
            loop.quit()

        agent.run(text, history=history or [],
                  on_delta=state["deltas"].append,
                  on_note=state["notes"].append,
                  on_done=on_done,
                  on_error=on_error)
        _wait(loop)
        return state

    # ---------------- 用例 ----------------
    def test_tool_call_then_reply(self):
        agent = Agent(self.app)
        state = self._run(agent, "记住我叫小张")

        self.assertIsNone(state["error"])
        self.assertEqual(state["done"], "记住啦，小张！")
        # 工具真的执行了：记忆里落库
        facts = {f["key"]: f["value"] for f in self.app.memory.facts()}
        self.assertEqual(facts.get("名字"), "小张")
        # 对话框收到了"记住了"旁白
        self.assertTrue(any("记住了" in n for n in state["notes"]))
        # 流式增量确实到了
        self.assertTrue(state["deltas"])
        self.assertEqual("".join(state["deltas"]), "记住啦，小张！")
        # 第二次请求带上了 role=tool 的结果
        self.assertTrue(any(m.get("role") == "tool"
                            for m in self.server.requests[-1]["messages"]))

    def test_tools_are_offered(self):
        agent = Agent(self.app)
        self._run(agent, "你好")
        first = self.server.requests[0]
        names = [t["function"]["name"] for t in (first.get("tools") or [])]
        self.assertIn("remember", names)
        self.assertIn("recall", names)
        self.assertIn("say", names)
        self.assertIn("move", names)
        self.assertEqual(first.get("tool_choice"), "auto")

    def test_system_prompt_has_env_and_memory(self):
        self.app.memory.remember("名字", "小张")
        agent = Agent(self.app)
        self._run(agent, "还记得我叫什么")
        sys_msg = self.server.requests[0]["messages"][0]
        self.assertEqual(sys_msg["role"], "system")
        self.assertIn("可用工具", sys_msg["content"])
        self.assertIn("长期记忆", sys_msg["content"])
        self.assertIn("小张", sys_msg["content"])

    def test_non_stream_mode(self):
        self.app.cfg["agent_stream"] = False
        self.server.mode = "text_only"
        self.server.final_text = "你好呀"
        agent = Agent(self.app)
        state = self._run(agent, "你好")
        self.assertEqual(state["done"], "你好呀")
        self.assertFalse(self.server.requests[0].get("stream"), "应走非流式")

    def test_unavailable_without_key(self):
        self.app.cfg["ai_key"] = ""
        agent = Agent(self.app)
        state = {"error": None}
        agent.run("hi", [], on_error=lambda e: state.__setitem__("error", e))
        self.assertIsNotNone(state["error"])

    def test_agent_disabled_still_chats(self):
        self.app.cfg["agent_enabled"] = False
        agent = Agent(self.app)
        self._run(agent, "你好")
        self.assertNotIn("tools", self.server.requests[0], "关掉 agent 后不应下发工具")


if __name__ == "__main__":
    unittest.main(verbosity=2)
