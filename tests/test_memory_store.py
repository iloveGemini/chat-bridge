"""
memory_store 回归测试（纯标准库 unittest，无网络）。

覆盖 ROADMAP P2 要求：迁移(建表/列) / 写入 / 召回 / 去重 / 降级，
外加按会话隔离引入的 fork / migrate / delete 作用域操作与前缀防串。

跑法：
    python tests/test_memory_store.py
    （或 python -m unittest discover -s tests）
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import memory_store as M  # noqa: E402


class MemoryStoreTestBase(unittest.TestCase):
    def setUp(self):
        # 每个用例一个独立临时库，互不污染
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)  # init_db 自己建
        M.init_db(self.db_path)

    def tearDown(self):
        M.close_db()
        for ext in ("", "-wal", "-shm"):
            p = self.db_path + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


class TestSchemaMigration(MemoryStoreTestBase):
    """建表幂等 + 历史迁移列存在。"""

    def _cols(self, table):
        return {r[1] for r in M._conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def test_tables_created(self):
        names = {r[0] for r in M._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ("events", "chunks", "facts", "summaries", "meta"):
            self.assertIn(t, names)

    def test_migrated_columns_present(self):
        self.assertIn("speaker", self._cols("chunks"))
        for c in ("scene_id", "time_label", "place_label"):
            self.assertIn(c, self._cols("events"))
        self.assertIn("is_state", self._cols("facts"))

    def test_init_is_idempotent(self):
        # 重复 init 不应抛错或重置数据
        M.upsert_fact("char:x", "甲", "喜欢", "茶")
        M.init_db(self.db_path)
        self.assertEqual(len(M.get_facts("char:x")), 1)


class TestWriteRead(MemoryStoreTestBase):
    """写入与读取：事件 / 切片 / 事实。"""

    def test_event_and_chunk_write(self):
        M.upsert_event("s", "雨夜初遇", session_id="s", importance=5, embedding=[1, 0, 0])
        M.add_chunk("s", "他把伞倾向我", session_id="s", speaker="许今闻", embedding=[1, 0, 0])
        self.assertEqual(len(M.list_memories("s", "events")), 1)
        self.assertEqual(len(M.list_memories("s", "chunks")), 1)

    def test_fact_kv_overwrite(self):
        M.upsert_fact("s", "许今闻", "擅长", "古典油画")
        M.upsert_fact("s", "许今闻", "擅长", "水彩")  # 同 SPO → 覆盖
        facts = [f for f in M.get_facts("s") if f["subject"] == "许今闻"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["object"], "水彩")

    def test_fact_retract(self):
        M.upsert_fact("s", "许今闻", "厌恶", "下雨")
        M.upsert_fact("s", "许今闻", "厌恶", retracted=True)
        preds = {(f["subject"], f["predicate"]) for f in M.get_facts("s")}
        self.assertNotIn(("许今闻", "厌恶"), preds)

    def test_fact_prune_keeps_state(self):
        M.upsert_fact("s", "许今闻", "身份", "画家", is_state=True)  # 锁死，免疫清理
        for i in range(90):
            M.upsert_fact("s", "杂项", f"偏好{i}", f"值{i}")
        M.prune_facts("s", max_limit=80)
        survivors = M.get_facts("s")
        self.assertLessEqual(len(survivors), 80)
        self.assertTrue(any(f["predicate"] == "身份" for f in survivors),
                        "is_state 核心事实不应被容量清理删除")


class TestRecallAndDegrade(MemoryStoreTestBase):
    """召回：向量模式命中最相似；无向量时降级为关键词。"""

    def _seed(self):
        M.upsert_event("s", "雨夜的争执", session_id="s", embedding=[1, 0, 0])
        M.upsert_event("s", "画室里的告白", session_id="s", embedding=[0, 1, 0])

    def test_vector_recall_orders_by_similarity(self):
        self._seed()
        top = M.recall_events("s", query_vec=[1, 0, 0], k=1)
        self.assertEqual(len(top), 1)
        self.assertIn("争执", top[0]["summary"])

    def test_keyword_degrade_without_vector(self):
        self._seed()
        res = M.recall_events("s", query_vec=None, query_text="画室", k=3)
        self.assertTrue(any("画室" in e["summary"] for e in res),
                        "无向量时应能用关键词命中")

    def test_build_context_vector_vs_degraded(self):
        self._seed()
        M.upsert_fact("s", "许今闻", "擅长", "油画")
        M.upsert_summary("arc:s", "宿命契约者")
        ctx_vec = M.build_memory_context("s", "s", query_vec=[0, 1, 0])
        ctx_kw = M.build_memory_context("s", "s", query_vec=None, query_text="画室")
        for ctx in (ctx_vec, ctx_kw):
            self.assertIn("宿命契约者", ctx)      # 关系弧恒定注入
            self.assertIn("许今闻", ctx)          # 硬事实恒定注入


class TestChunkDedup(MemoryStoreTestBase):
    """切片去重：细节若是某事件摘要的子串则丢弃。"""

    def test_chunk_substring_of_event_dropped(self):
        phrase = "他把伞向我倾斜"
        M.upsert_event("s", f"雨夜里，{phrase}，我心头一颤。", session_id="s", embedding=[1, 0, 0])
        M.add_chunk("s", phrase, session_id="s", embedding=[1, 0, 0])  # 子串
        M.add_chunk("s", "画室挂满向日葵油画", session_id="s", embedding=[1, 0, 0])  # 非子串
        diag = {}
        M.build_memory_context("s", "s", query_vec=[1, 0, 0], diag=diag)
        dropped = {c["snippet"]: c["dropped"] for c in diag["chunks"]}
        self.assertTrue(dropped.get(phrase[:32]), "事件子串细节应被去重丢弃")
        self.assertFalse(dropped.get("画室挂满向日葵油画"[:32]), "非子串细节应保留")


class TestScopeOps(MemoryStoreTestBase):
    """按会话隔离：fork(克隆) / migrate(改名) / delete(删除) + 前缀防串。"""

    def _seed(self, scope="sess:A", sid="A"):
        M.upsert_event(scope, "初遇", session_id=sid, importance=5, embedding=[1, 0, 0])
        M.add_chunk(scope, "他把伞倾向我", session_id=sid, embedding=[1, 0, 0])
        M.upsert_fact(scope, "许今闻", "擅长", "油画", is_state=True)
        M.upsert_summary(f"arc:{scope}", "宿命契约者")
        M.upsert_summary(f"session:{sid}", "近况：刚下过雨")
        M.set_meta(f"summ:{sid}", {"boundary": 10, "state": "idle"})

    def test_new_scope_is_empty(self):
        self._seed()
        self.assertEqual(M.list_memories("sess:B"), [])
        self.assertIsNone(M.get_summary("arc:sess:B"))

    def test_fork_inherits_then_independent(self):
        self._seed()
        self.assertEqual(M.fork_scope("sess:A", "sess:B", "A", "B")["ok"], True)
        # 继承
        self.assertEqual(len(M.list_memories("sess:B", "events")), 1)
        self.assertEqual(len(M.get_facts("sess:B")), 1)
        self.assertEqual(M.get_summary("arc:sess:B"), "宿命契约者")
        self.assertEqual(M.get_summary("session:B"), "近况：刚下过雨")
        self.assertEqual(M.get_meta("summ:B")["boundary"], 10)
        # fork 后互不影响
        M.upsert_event("sess:B", "克隆体新剧情", session_id="B", embedding=[0, 1, 0])
        self.assertEqual(len(M.list_memories("sess:A", "events")), 1)
        self.assertEqual(len(M.list_memories("sess:B", "events")), 2)

    def test_migrate_moves_in_place(self):
        self._seed()
        M.migrate_scope("sess:A", "sess:A2", "A", "A2")
        self.assertEqual(M.list_memories("sess:A", "events"), [])
        self.assertIsNone(M.get_summary("arc:sess:A"))
        self.assertEqual(len(M.list_memories("sess:A2", "events")), 1)
        self.assertEqual(M.get_summary("arc:sess:A2"), "宿命契约者")
        self.assertEqual(M.get_summary("session:A2"), "近况：刚下过雨")
        self.assertEqual(M.get_meta("summ:A2")["boundary"], 10)

    def test_delete_leaves_no_orphan(self):
        self._seed()
        M.delete_scope("sess:A", "A")
        self.assertEqual(M.list_memories("sess:A"), [])
        self.assertIsNone(M.get_summary("arc:sess:A"))
        self.assertIsNone(M.get_summary("session:A"))
        self.assertIsNone(M.get_meta("summ:A"))

    def test_prefix_does_not_collide(self):
        # sess:N_1 不应误配 sess:N_10 的关系弧
        M.upsert_summary("arc:sess:N_10", "别人的弧")
        self.assertEqual(M.list_memories("sess:N_1", "summaries"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
