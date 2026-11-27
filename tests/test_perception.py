# -*- coding: utf-8 -*-
"""桌面感知层测试：分类、快照、事件提炼（winapi 全部打桩）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ctx  # noqa: F401

from smartpet import perception as P


class ClassifyTest(unittest.TestCase):
    def test_code(self):
        self.assertEqual(P.classify("main.py - Visual Studio Code", "Chrome_WidgetWin_1"), "code")
        self.assertEqual(P.classify("PyCharm - proj", ""), "code")
        self.assertEqual(P.classify("Windows PowerShell", "ConsoleWindowClass"), "code")

    def test_game_media_chat_office(self):
        self.assertEqual(P.classify("Steam", ""), "game")
        self.assertEqual(P.classify("哔哩哔哩 (゜-゜)つロ", ""), "media")
        self.assertEqual(P.classify("微信", ""), "chat")
        self.assertEqual(P.classify("报告.docx - Word", ""), "office")

    def test_browser_by_class_when_title_plain(self):
        self.assertEqual(P.classify("豆包", "Chrome_WidgetWin_1"), "browser")

    def test_other(self):
        self.assertEqual(P.classify("", ""), "other")
        self.assertEqual(P.classify("某个不认识的窗口", "SomeClass"), "other")


class SnapshotTest(unittest.TestCase):
    def test_context_text(self):
        snap = P.PerceptionSnapshot(ts=0, hour=23, idle_sec=5.0,
                                    app_title="main.py - VS Code",
                                    app_class="Chrome_WidgetWin_1",
                                    app_category="code", cpu=35.0,
                                    code_minutes=42, song="稻香",
                                    pet_state="idle", pet_mood=80, pet_energy=60)
        text = snap.context_text()
        self.assertIn("深夜", text)
        self.assertIn("写代码", text)
        self.assertIn("42 分钟", text)
        self.assertIn("稻香", text)
        self.assertIn("正在用电脑", text)

    def test_user_activity_levels(self):
        self.assertEqual(P.PerceptionSnapshot(idle_sec=5).user_activity(), "正在用电脑")
        self.assertEqual(P.PerceptionSnapshot(idle_sec=90).user_activity(), "停手发呆")
        self.assertEqual(P.PerceptionSnapshot(idle_sec=200).user_activity(), "离开了一会儿")
        self.assertEqual(P.PerceptionSnapshot(idle_sec=900).user_activity(), "离开很久了")


class BusTest(unittest.TestCase):
    """直接调用 _tick()，不启动定时器。"""

    def setUp(self):
        self.cfg = {"perception_enabled": True, "perception_interval": 3000}
        self.bus = P.PerceptionBus(self.cfg)
        self.events = []
        self.bus.event_occurred.connect(lambda k, t: self.events.append((k, t)))

    def _tick(self, title="main.py - VS Code", cls="Chrome_WidgetWin_1",
              fs=False, idle=5, cpu=20.0):
        with mock.patch.object(P.winapi, "IS_WINDOWS", True), \
             mock.patch.object(P.winapi, "foreground_info",
                               return_value={"title": title, "cls": cls,
                                             "fullscreen": fs}), \
             mock.patch.object(P.winapi, "get_idle_seconds", return_value=idle), \
             mock.patch.object(P.winapi, "cpu_percent", return_value=cpu):
            self.bus._tick()

    def test_snapshot_fields(self):
        self._tick()
        snap = self.bus.snapshot()
        self.assertEqual(snap.app_category, "code")
        self.assertEqual(snap.idle_sec, 5)
        self.assertEqual(snap.cpu, 20.0)
        self.assertFalse(snap.fullscreen)

    def test_app_switch_event(self):
        self._tick(title="main.py - VS Code", cls="Chrome_WidgetWin_1")
        self._tick(title="哔哩哔哩", cls="Chrome_WidgetWin_1")
        kinds = [k for k, _ in self.events]
        self.assertIn("app_switch", kinds)
        self.assertTrue(any("看视频" in t or "浏览网页" in t for _k, t in self.events))

    def test_fullscreen_event(self):
        self._tick(fs=False)
        self._tick(fs=True)
        self.assertIn("fullscreen", [k for k, _ in self.events])

    def test_idle_transitions(self):
        self._tick(idle=5)
        self._tick(idle=200)
        self.assertIn("idle_away", [k for k, _ in self.events])
        self._tick(idle=1)
        self.assertIn("idle_back", [k for k, _ in self.events])

    def test_music_and_clipboard(self):
        self.bus.set_music("稻香 - 周杰伦")
        self.bus.note_clipboard("一段复制内容")
        kinds = [k for k, _ in self.events]
        self.assertIn("music_change", kinds)
        self.assertIn("clipboard", kinds)

    def test_survives_winapi_failure(self):
        with mock.patch.object(P.winapi, "IS_WINDOWS", True), \
             mock.patch.object(P.winapi, "foreground_info", side_effect=OSError("x")), \
             mock.patch.object(P.winapi, "get_idle_seconds", side_effect=OSError("x")), \
             mock.patch.object(P.winapi, "cpu_percent", side_effect=OSError("x")):
            self.bus._tick()   # 不应抛异常
        self.assertIsNotNone(self.bus.snapshot())


if __name__ == "__main__":
    unittest.main(verbosity=2)
