# -*- coding: utf-8 -*-
"""测试公共引导：把 LineDogPetv2.8.3 加进 sys.path，并给 Qt 用 offscreen。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.join(ROOT, "LineDogPetv2.8.3")

if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_app_stub(tmpdir):
    """给 Agent/Tool 用的最小 PetApp 替身（不建窗口、不起托盘）。"""
    import types

    from smartpet.memory import MemoryStore
    from smartpet.perception import PerceptionBus

    class FakePet:
        state = "idle"
        width = 150
        height = 150
        x = 100
        y = 100

        def __init__(self):
            self.bubbles = []
            self.moves = []
            self.replays = 0
            self.sleeping = False

        def show_bubble(self, text, ms=None):
            self.bubbles.append(text)

        def relocate(self, cx, cy, speed=3.2, immediate=False, force=False):
            self.moves.append((cx, cy))
            return True

        def replay(self):
            self.replays += 1

        def wake(self):
            self.sleeping = False

        def go_sleep(self, deep=False):
            self.sleeping = True

        def frameGeometry(self):
            class _R:
                def center(self):
                    class _P:
                        x, y = 100, 100
                    return _P()
            return _R()

    app = types.SimpleNamespace()
    app.cfg = {
        "character": "xiaobai",
        "mood": 70.0,
        "energy": 60.0,
        "size": 150,
        "memory_enabled": True,
        "agent_enabled": True,
        "agent_stream": True,
        "agent_max_rounds": 3,
        "ai_context": True,
        "ai_max_tokens": 200,
        "ai_temperature": 0.5,
        "perception_enabled": False,
    }
    app.pet = FakePet()
    app.memory = MemoryStore(path=os.path.join(tmpdir, "memory.db"))
    app.perception = PerceptionBus(app.cfg)
    app.perception.set_pet_provider(
        lambda: (app.pet.state, app.cfg["mood"], app.cfg["energy"]))
    app.saved = []
    app.save = lambda: app.saved.append(True)
    app.feeds = []
    app.feed = lambda: app.feeds.append(True)
    app.plays = []
    app.play = lambda: app.plays.append("play")
    app.start_chase = lambda: app.plays.append("chase")
    app.start_hide = lambda: app.plays.append("hide")
    app.visibility = []
    app.set_pet_visible = lambda v: app.visibility.append(v)
    app.opened = []
    app.open_diary = lambda: app.opened.append("diary")
    app.open_status = lambda: app.opened.append("status")
    app.open_settings = lambda focus: app.opened.append("settings")
    app.open_chat = lambda: app.opened.append("chat")
    return app
