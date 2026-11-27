# -*- coding: utf-8 -*-
"""AI 长期记忆。

三层结构，全部落在本地 sqlite（默认 D:\\LineDogPet\\memory.db）：

    facts     结构化事实：{key: value}，有 kind/source/confidence，key 唯一、可覆盖
    episodes  经历流水：谁在什么时候做了什么（喂食、聊天、里程碑…），可检索
    meta      杂项：最近摘要时间、计数等

对上层暴露两件事：
    remember() / forget() / facts()      —— 读写事实
    context_text(budget)                 —— 渲染成可直接塞进 system prompt 的短文本

设计取舍：
- 不上向量库：数据量小（几百条），LIKE + 关键词召回足够，且零依赖、零额外内存；
- 强制容量上限（facts 200 / episodes 800），超限按时间淘汰，避免无限膨胀；
- 所有写入都是短事务，异常一律吞掉并返回 False，绝不让记忆故障影响桌宠主流程。
"""
import json
import os
import sqlite3
import time

from . import config as cfgmod

_DDL = [
    """CREATE TABLE IF NOT EXISTS facts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE,
        value TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'fact',
        source TEXT NOT NULL DEFAULT 'user',
        confidence REAL NOT NULL DEFAULT 1.0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS episodes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        summary TEXT NOT NULL,
        meta TEXT)""",
    """CREATE TABLE IF NOT EXISTS meta(
        key TEXT PRIMARY KEY,
        value TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_facts_kind ON facts(kind)",
]

# 这些事件对"回忆"有价值，其余高频噪声不落库
EPISODE_KINDS = {
    "feed": "喂了小狗",
    "play": "陪小狗玩",
    "chase": "陪小狗追球",
    "hide": "和小狗玩躲猫猫",
    "chat": "和用户聊天",
    "milestone": "解锁了里程碑",
    "app_switch": None,     # None = 不记（太频繁）
}


def _now():
    return time.time()


def _hms(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts or _now()))


class MemoryStore:
    """本地长期记忆。线程不安全，约定只在 GUI 线程使用。"""

    def __init__(self, path=None):
        self.path = path or os.path.join(cfgmod.data_dir(), "memory.db")
        self.max_facts = 200
        self.max_episodes = 800
        self._con = None
        self._ready = False

    # ---------------- 生命周期 ----------------
    def _conn(self):
        if self._con is None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._con = sqlite3.connect(self.path, timeout=5)
            self._con.row_factory = sqlite3.Row
            for ddl in _DDL:
                self._con.execute(ddl)
            self._con.commit()
        return self._con

    def close(self):
        if self._con is not None:
            try:
                self._con.commit()
                self._con.close()
            except Exception:
                pass
            self._con = None

    def available(self):
        try:
            self._conn()
            return True
        except Exception:
            return False

    # ---------------- 事实 ----------------
    def remember(self, key, value, kind="fact", source="user", confidence=1.0):
        """写入/覆盖一条事实。返回是否成功。"""
        key = (key or "").strip()[:80]
        value = (value or "").strip()[:600]
        if not key or not value:
            return False
        try:
            c = self._conn()
            now = _now()
            c.execute(
                "INSERT INTO facts(key,value,kind,source,confidence,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " kind=excluded.kind, source=excluded.source,"
                " confidence=excluded.confidence, updated_at=excluded.updated_at",
                (key, value, kind or "fact", source or "user",
                 float(confidence), now, now))
            c.commit()
            self._trim_facts()
            return True
        except Exception:
            return False

    def forget(self, key):
        try:
            c = self._conn()
            c.execute("DELETE FROM facts WHERE key=?", ((key or "").strip(),))
            c.commit()
            return True
        except Exception:
            return False

    def facts(self, kind=None, limit=50):
        try:
            c = self._conn()
            if kind:
                rows = c.execute(
                    "SELECT key,value,kind,source,confidence,updated_at FROM facts"
                    " WHERE kind=? ORDER BY updated_at DESC LIMIT ?", (kind, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT key,value,kind,source,confidence,updated_at FROM facts"
                    " ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _trim_facts(self):
        try:
            c = self._conn()
            n = c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            if n > self.max_facts:
                c.execute(
                    "DELETE FROM facts WHERE id IN ("
                    " SELECT id FROM facts ORDER BY updated_at ASC LIMIT ?)",
                    (n - self.max_facts,))
                c.commit()
        except Exception:
            pass

    # ---------------- 经历 ----------------
    def add_episode(self, kind, summary, meta=None):
        kind = (kind or "misc").strip()[:32]
        summary = (summary or "").strip()[:400]
        if not summary:
            return False
        try:
            c = self._conn()
            c.execute(
                "INSERT INTO episodes(ts,kind,summary,meta) VALUES(?,?,?,?)",
                (_now(), kind, summary,
                 json.dumps(meta, ensure_ascii=False) if meta else None))
            c.commit()
            self._trim_episodes()
            return True
        except Exception:
            return False

    def episodes(self, limit=10, kind=None):
        try:
            c = self._conn()
            if kind:
                rows = c.execute(
                    "SELECT ts,kind,summary FROM episodes WHERE kind=?"
                    " ORDER BY ts DESC LIMIT ?", (kind, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts,kind,summary FROM episodes"
                    " ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _trim_episodes(self):
        try:
            c = self._conn()
            n = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            if n > self.max_episodes:
                c.execute(
                    "DELETE FROM episodes WHERE id IN ("
                    " SELECT id FROM episodes ORDER BY ts ASC LIMIT ?)",
                    (n - self.max_episodes,))
                c.commit()
        except Exception:
            pass

    # ---------------- 检索 ----------------
    def search(self, query, limit=6):
        """关键词召回：命中 key/value 的事实 + 命中 summary 的经历。"""
        q = (query or "").strip()
        if not q:
            return {"facts": [], "episodes": []}
        # 中文没有空格分词，这里额外按 2 字滑窗做粗召回
        tokens = [q[i:i + 2] for i in range(0, max(1, len(q) - 1), 2)][:6] or [q]
        try:
            c = self._conn()
            fts, eps = [], []
            for tok in [q] + tokens:
                lk = "%" + tok + "%"
                fts += c.execute(
                    "SELECT key,value,kind FROM facts"
                    " WHERE key LIKE ? OR value LIKE ? LIMIT ?",
                    (lk, lk, limit)).fetchall()
                eps += c.execute(
                    "SELECT ts,kind,summary FROM episodes"
                    " WHERE summary LIKE ? ORDER BY ts DESC LIMIT ?",
                    (lk, limit)).fetchall()
                if len(fts) >= limit and len(eps) >= limit:
                    break
            # 去重（按文本）
            seen, uf = set(), []
            for r in fts:
                k = (r["key"], r["value"])
                if k not in seen:
                    seen.add(k)
                    uf.append(dict(r))
            seen2, ue = set(), []
            for r in eps:
                k = r["summary"]
                if k not in seen2:
                    seen2.add(k)
                    ue.append(dict(r))
            return {"facts": uf[:limit], "episodes": ue[:limit]}
        except Exception:
            return {"facts": [], "episodes": []}

    # ---------------- 渲染 ----------------
    def context_text(self, budget=900, query=None):
        """把记忆渲染成一段短文本，供 system prompt 使用。

        query 非空时优先放"相关"内容，否则放最近更新的。
        """
        lines = []
        used_keys = set()
        if query:
            hit = self.search(query, limit=4)
            if hit["facts"]:
                used_keys = {r["key"] for r in hit["facts"]}
                lines.append("相关事实：" + "；".join(
                    "%s=%s" % (r["key"], r["value"]) for r in hit["facts"]))
            if hit["episodes"]:
                lines.append("相关回忆：" + "；".join(
                    "%s %s" % (_hms(r["ts"]), r["summary"]) for r in hit["episodes"]))
        facts = [f for f in self.facts(limit=12) if f["key"] not in used_keys]
        if facts:
            lines.append("关于用户：" + "；".join(
                "%s=%s" % (f["key"], f["value"]) for f in facts[:8]))
        eps = self.episodes(limit=6)
        if eps:
            lines.append("最近经历：" + "；".join(
                "%s %s" % (_hms(e["ts"]), e["summary"]) for e in eps[:5]))
        text = "\n".join(lines).strip()
        if len(text) > budget:
            text = text[:budget] + "…"
        return text

    def stats(self):
        try:
            c = self._conn()
            return {
                "facts": c.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                "episodes": c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0],
                "path": self.path,
            }
        except Exception:
            return {"facts": 0, "episodes": 0, "path": self.path}

    def clear(self, what="all"):
        """清空记忆。what: facts / episodes / all。"""
        try:
            c = self._conn()
            if what in ("facts", "all"):
                c.execute("DELETE FROM facts")
            if what in ("episodes", "all"):
                c.execute("DELETE FROM episodes")
            c.commit()
            return True
        except Exception:
            return False
