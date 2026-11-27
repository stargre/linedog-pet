# -*- coding: utf-8 -*-
"""MemoryStore 单元测试（不依赖 Qt / 网络）。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ctx  # noqa: F401  (负责把 App 目录加进 sys.path)

from smartpet.memory import MemoryStore


class MemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ldp_mem_")
        self.mem = MemoryStore(path=os.path.join(self.tmp, "memory.db"))
        self.assertTrue(self.mem.available())

    def tearDown(self):
        self.mem.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_remember_and_overwrite(self):
        self.assertTrue(self.mem.remember("名字", "小张"))
        self.assertTrue(self.mem.remember("名字", "老张"))
        facts = self.mem.facts()
        self.assertEqual(len(facts), 1, "同名 key 应该覆盖而不是新增")
        self.assertEqual(facts[0]["value"], "老张")

    def test_empty_input_rejected(self):
        self.assertFalse(self.mem.remember("", "x"))
        self.assertFalse(self.mem.remember("k", ""))

    def test_forget(self):
        self.mem.remember("怕黑", "是")
        self.assertTrue(self.mem.forget("怕黑"))
        self.assertEqual(self.mem.facts(), [])

    def test_episodes(self):
        self.mem.add_episode("feed", "喂了小狗")
        self.mem.add_episode("play", "陪小狗玩")
        eps = self.mem.episodes(limit=5)
        self.assertEqual(len(eps), 2)
        self.assertEqual(eps[0]["summary"], "陪小狗玩", "应按时间倒序")

    def test_search_chinese(self):
        self.mem.remember("职业", "后端工程师")
        self.mem.add_episode("chat", "用户说最近在学 Rust")
        hit = self.mem.search("Rust")
        self.assertTrue(hit["episodes"], "应能按关键词召回经历")
        hit2 = self.mem.search("工程师")
        self.assertTrue(hit2["facts"], "应能按关键词召回事实")

    def test_context_text(self):
        self.mem.remember("名字", "小张")
        self.mem.add_episode("feed", "喂了小狗")
        text = self.mem.context_text()
        self.assertIn("小张", text)
        self.assertIn("喂了小狗", text)

    def test_context_budget(self):
        for i in range(30):
            self.mem.remember("k%d" % i, "v" * 50)
        text = self.mem.context_text(budget=200)
        self.assertLessEqual(len(text), 201)

    def test_trim_facts(self):
        self.mem.max_facts = 5
        for i in range(12):
            self.mem.remember("k%d" % i, "v%d" % i)
        self.assertLessEqual(len(self.mem.facts(limit=100)), 5)

    def test_stats_and_clear(self):
        self.mem.remember("a", "b")
        self.mem.add_episode("chat", "hello")
        st = self.mem.stats()
        self.assertEqual(st["facts"], 1)
        self.assertEqual(st["episodes"], 1)
        self.mem.clear()
        st2 = self.mem.stats()
        self.assertEqual((st2["facts"], st2["episodes"]), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
