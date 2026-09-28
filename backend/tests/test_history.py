"""删除历史记录与贡献统计的单元测试(独立临时文件, 不触碰真实数据)"""
import json
import os
import shutil
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import history as hist  # noqa: E402


class HistoryBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="deepclean_history_")
        self.path = os.path.join(self.dir, "history.json")
        os.environ["DEEPCLEAN_HISTORY_PATH"] = self.path
        self.addCleanup(os.environ.pop, "DEEPCLEAN_HISTORY_PATH", None)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def record(self, source="file_scan", mode="permanent", items=None,
               app=None, ok=True, detail=""):
        if items is None:
            items = [{"path": "/tmp/a.bin", "size": 1024}]
        return hist.record(source=source, mode=mode, items=items,
                           app=app, ok=ok, detail=detail)


class TestHistoryBasics(HistoryBase):

    def test_record_list_stats_clear(self):
        # 初始为空
        self.assertEqual(hist.list_records(), {"records": [], "total": 0})
        st = hist.stats()
        self.assertEqual(st["operations"], 0)
        self.assertEqual(st["bytes_freed"], 0)

        r1 = self.record()
        r2 = self.record(source="app_uninstall", mode="trash",
                         items=[{"path": "/Applications/X.app", "size": 100, "category": "应用本体"},
                                {"path": "/Users/a/Library/Preferences/x.plist", "size": 5}],
                         app={"name": "X", "bundle_id": "com.x.app", "aliases": ["X"]})
        self.assertEqual(r1["count"], 1)
        self.assertEqual(r1["bytes"], 1024)

        # 最新在前 + 分页 + 来源过滤
        page = hist.list_records(limit=10)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["records"][0]["id"], r2["id"])
        self.assertEqual(hist.list_records(source="app_uninstall")["total"], 1)
        self.assertEqual(hist.list_records(limit=1, offset=1)["total"], 2)

        # 统计口径
        st = hist.stats()
        self.assertEqual(st["operations"], 2)
        self.assertEqual(st["items_deleted"], 3)
        self.assertEqual(st["bytes_freed"], 1024 + 105)
        self.assertEqual(st["apps_uninstalled"], 1)
        self.assertEqual(st["residues_cleared"], 1)  # 应用本体不计入残留
        self.assertEqual(st["by_source"]["app_uninstall"]["operations"], 1)

        # 持久化: 重新读盘
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["records"]), 2)

        # 清空
        self.assertEqual(hist.clear(), 2)
        self.assertEqual(hist.list_records()["total"], 0)
        self.assertEqual(hist.stats()["operations"], 0)

    def test_failed_record_not_counted_in_stats(self):
        self.record(ok=False, detail="部分失败")
        self.record()  # 成功的那一次
        st = hist.stats()
        self.assertEqual(st["operations"], 1)
        self.assertEqual(hist.list_records()["total"], 2)  # 但列表仍能看到失败记录

    def test_items_and_records_caps(self):
        items = [{"path": f"/tmp/f{i}.bin", "size": 10} for i in range(600)]
        r = self.record(items=items)
        self.assertEqual(r["count"], 600)
        self.assertEqual(r["bytes"], 6000)
        self.assertEqual(len(r["items"]), hist._MAX_ITEMS)
        self.assertTrue(r["items_truncated"])
        # 截断不影响残留统计兜底
        self.assertGreaterEqual(hist.stats()["residues_cleared"], 0)

        for _ in range(hist._MAX_RECORDS + 5):
            hist.record("file_scan", "permanent", [{"path": "/tmp/x", "size": 1}])
        self.assertEqual(hist.list_records(limit=1)["total"], hist._MAX_RECORDS)

    def test_corrupt_file_treated_as_empty(self):
        with open(self.path, "w") as f:
            f.write("{not-json!!")
        self.assertEqual(hist.list_records()["total"], 0)
        self.assertEqual(hist.stats()["operations"], 0)
        # 之后仍可正常记录
        self.record()
        self.assertEqual(hist.list_records()["total"], 1)

    def test_evidence_extraction(self):
        self.record(source="app_uninstall", mode="trash",
                    items=[{"path": "/Applications/Old.app", "size": 1, "category": "应用本体"}],
                    app={"name": "OldApp", "bundle_id": "com.old.app",
                         "aliases": ["OldApp", "旧应用"]})
        self.record(source="file_scan", mode="permanent",
                    items=[{"path": "/Users/a/Downloads/MyBigCache.zip", "size": 9}])
        self.record(source="file_scan", mode="permanent",
                    items=[{"path": "/Users/a/x"}], ok=False)  # 失败的不作为证据

        bids, names = hist.app_evidence()
        self.assertEqual(bids, {"com.old.app"})
        self.assertEqual(names, {"OldApp", "旧应用"})
        stems = hist.file_evidence()
        self.assertIn("mybigcache.zip", stems)
        self.assertIn("mybigcache", stems)
        self.assertNotIn("x", stems)  # len<4 / 失败记录不入证据


if __name__ == "__main__":
    unittest.main()
