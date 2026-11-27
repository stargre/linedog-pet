# -*- coding: utf-8 -*-
"""端到端人工验证：真实 PetApp（离屏）+ mock LLM，跑通"输入 -> 工具调用 -> 记忆 -> 气泡"。

不是 unittest 用例（名字不以 test_ 开头），需要手动跑：
    python tests/e2e_chat.py
它会用临时数据目录，不污染 D:\\LineDogPet。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ctx  # noqa: F401

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from mock_llm import MockLLM
from smartpet import config as cfgmod

TMP = tempfile.mkdtemp(prefix="ldp_e2e_")
# 让整包数据落临时目录，避免动真实 D:\LineDogPet
cfgmod.DATA_ROOT = TMP
cfgmod._RESOLVED_CONFIG = None

from smartpet.app import PetApp   # noqa: E402  (必须在改 DATA_ROOT 之后导入)

OK = []
FAIL = []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)) if detail else ""))


def main():
    server = MockLLM(mode="tool_then_text", final_text="记住啦，小张！").start()
    qapp = QApplication(sys.argv)
    a = PetApp(qapp)

    # 配好 AI（指向 mock）并关掉感知采样，避免干扰
    a.cfg.update({"ai_enabled": True, "ai_base": server.base_url,
                  "ai_key": "test-key", "ai_model": "mock-model",
                  "agent_stream": True, "perception_enabled": False})
    check("Agent 可用", a.agent.available())
    check("工具已装配", "remember" in a.agent.tool_names(), a.agent.tool_names())
    check("感知快照可读", isinstance(a.perception.snapshot().app_category, str))

    # 打开对话窗并投递一句话
    a.open_chat()
    check("对话窗已创建", a.chat is not None)

    state = {"done": None}
    loop = QEventLoop()

    orig = a._agent_replied

    def wrapped(text):
        orig(text)
        state["done"] = text
        QTimer.singleShot(0, loop.quit)

    a._agent_replied = wrapped

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(20000)

    a.handle_chat("记住我叫小张，我喜欢喝美式")
    loop.exec()
    timer.stop()

    view_text = a.chat.view.toPlainText()
    check("收到最终回复", state["done"] == "记住啦，小张！", state["done"])
    check("对话窗有内容", "记住啦" in view_text, view_text[:120])
    check("对话窗有工具旁白", "记住了" in view_text)

    facts = {f["key"]: f["value"] for f in a.memory.facts()}
    check("记忆落库", facts.get("名字") == "小张", facts)
    check("记忆库文件已建", os.path.isfile(a.memory.path), a.memory.path)

    check("对话已进历史", any(m.get("role") == "assistant" for m in a.ai_history))
    check("请求带工具定义",
          any(t["function"]["name"] == "remember"
              for t in (server.requests[0].get("tools") or [])))
    check("第二次请求带 tool 结果",
          any(m.get("role") == "tool" for m in server.requests[-1]["messages"]))

    # 记忆跨会话：换一个 Agent 实例也能读到
    from smartpet.agent import Agent
    a2 = Agent(a)
    prompt = a2._system_prompt("我叫什么")
    check("新会话能带上旧记忆", "小张" in prompt, prompt[:200])

    # 设置面板：新加的 Agent 开关与记忆状态要能正常构建
    from smartpet.panels import SettingsDialog
    dlg = SettingsDialog(a.cfg, a.assets, None, a.plugins, "")
    check("设置面板可构建", dlg is not None)
    check("Agent 开关存在", hasattr(dlg, "agent_on") and dlg.agent_on.isChecked())
    check("记忆状态显示", "记忆库" in dlg.mem_status.text(), dlg.mem_status.text())
    check("记忆状态有数字", "1 条事实" in dlg.mem_status.text(), dlg.mem_status.text())
    dlg.reject()

    a.quit_app()
    server.stop()
    qapp.processEvents()

    print("\n%d passed, %d failed" % (len(OK), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
