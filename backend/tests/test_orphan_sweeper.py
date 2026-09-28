"""无主残留(孤儿残留)扫描与清理的端到端测试 —— 沙盒仿真 macOS 目录树, 不触碰真实数据"""
import dataclasses
import os
import shutil
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import history as hist                      # noqa: E402
import orphan_sweeper as osw                # noqa: E402
import app_uninstaller as au                # noqa: E402
from tests.test_app_uninstaller import build_sandbox  # noqa: E402


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class OrphanBase(unittest.TestCase):
    """独立沙盒 + 独立历史文件 + 无主残留 fixture + 历史证据"""

    def setUp(self):
        self.layout, self.p = build_sandbox()
        self.addCleanup(shutil.rmtree, self.p["root"], True)

        hdir = tempfile.mkdtemp(prefix="deepclean_orphan_hist_")
        os.environ["DEEPCLEAN_HISTORY_PATH"] = os.path.join(hdir, "history.json")
        self.addCleanup(os.environ.pop, "DEEPCLEAN_HISTORY_PATH", None)
        self.addCleanup(shutil.rmtree, hdir, True)

        self.fx = self._make_orphans()
        # 历史证据: 本软件卸载过 OldApp(bundle+别名), 删过 MyBigCache.zip
        hist.record("app_uninstall", "trash",
                    items=[{"path": "/Applications/OldApp.app", "size": 1000,
                            "category": "应用本体"}],
                    app={"name": "OldApp", "bundle_id": "com.old.app",
                         "aliases": ["OldApp"]})
        hist.record("file_scan", "permanent",
                    items=[{"path": "/Users/x/Downloads/MyBigCache.zip", "size": 500}])

    def _make_orphans(self) -> dict:
        lib, home, root = self.p["lib"], self.p["home"], self.p["root"]
        recv = os.path.join(root, "private", "var", "db", "receipts")
        crash = os.path.join(lib, "Application Support", "CrashReporter")
        fx = {
            # 无主 Bundle ID(应用已不存在) → 应命中
            "bundle_pref": os.path.join(lib, "Preferences", "com.gone.app.plist"),
            "bundle_electron": os.path.join(lib, "Caches",
                                            "com.electron.lark.font_workaround"),
            "bundle_receipt": os.path.join(recv, "com.gone.app.bom"),
            "bundle_group": os.path.join(lib, "Group Containers",
                                         "ABCDE12345.com.gone.app"),
            # 历史应用相关 → history 层
            "hist_level2": os.path.join(crash, "OldApp_61A1.plist"),
            "hist_name": os.path.join(lib, "Application Support", "OldApp.cache"),
            "hist_bundle": os.path.join(lib, "Preferences", "com.old.app.x.plist"),
            "hist_dotfile": os.path.join(home, ".oldapp"),
            # 历史已删除文件 → file_history 层(疑似, 默认不勾选)
            "file_cache": os.path.join(lib, "Caches", "MyBigCache"),
            # 系统组件 → 必须被排除
            "sys_pref": os.path.join(lib, "Preferences", "com.apple.system.plist"),
            "sys_receipt": os.path.join(recv, "com.apple.installer.bom"),
            # 归属已安装应用 → 不是残留
            "owned_foo": os.path.join(lib, "Caches", "com.vendor.foo"),
            "owned_baz_receipt": os.path.join(recv, "com.vendor.baz.bom"),
            # 无任何证据 → 不列出
            "no_evidence": os.path.join(lib, "Application Support", "LeftoverTool"),
        }
        DIR_KEYS = {"hist_name", "hist_dotfile", "owned_foo", "no_evidence"}
        for key, path in fx.items():
            if key in DIR_KEYS:
                os.makedirs(path, exist_ok=True)
                _write(os.path.join(path, "inner.bin"), b"y" * 8)
            else:
                _write(path, b"z" * 64)
        return fx


class TestOrphanScan(OrphanBase):

    def test_scan_evidence_tiers_and_exclusions(self):
        result = osw.scan(self.layout)
        by_path = {i["path"]: i for i in result["items"]}

        # ① Bundle ID 无主 → match=bundle, 已安装清单非空 → 默认勾选
        for key in ("bundle_pref", "bundle_electron", "bundle_receipt", "bundle_group"):
            p = self.fx[key]
            self.assertIn(p, by_path, f"缺少无主残留: {p}")
            it = by_path[p]
            self.assertEqual(it["match"], "bundle", p)
            self.assertTrue(it["selected"], p)
            self.assertIn("未找到对应应用", it["evidence"])

        # ② 历史卸载应用 → history, 默认勾选; 二级目录也要能发现
        for key in ("hist_level2", "hist_name", "hist_bundle", "hist_dotfile"):
            p = self.fx[key]
            self.assertIn(p, by_path, f"缺少历史残留: {p}")
            self.assertEqual(by_path[p]["match"], "history", p)
            self.assertTrue(by_path[p]["selected"], p)
        self.assertEqual(by_path[self.fx["hist_bundle"]]["evidence"],
                         "本软件卸载过的应用")

        # ③ 历史已删除文件 → file_history, 默认不勾选
        p = self.fx["file_cache"]
        self.assertIn(p, by_path)
        self.assertEqual(by_path[p]["match"], "file_history")
        self.assertFalse(by_path[p]["selected"], "疑似项不应默认勾选")

        # ④ 系统组件 / 归属已安装 / 无证据 → 都不出现
        for key in ("sys_pref", "sys_receipt", "owned_foo",
                    "owned_baz_receipt", "no_evidence"):
            self.assertNotIn(self.fx[key], by_path, f"不应列出: {key}")

        # 系统排除计数与汇总
        self.assertEqual(result["summary"]["excluded_system"], 2)
        self.assertEqual(result["summary"]["count"], len(result["items"]))
        self.assertEqual(result["summary"]["selected_count"],
                         sum(1 for i in result["items"] if i["selected"]))
        self.assertTrue(any("排除 2 项系统组件" in w for w in result["warnings"]),
                        result["warnings"])

        # 默认勾选项排在疑似之前
        flags = [i["selected"] for i in result["items"]]
        self.assertEqual(flags, sorted(flags, reverse=True))

    def test_bundle_downgrade_when_no_app_inventory(self):
        """读不到已安装应用清单时, Bundle ID 判定不可靠 → 降级为默认不勾选"""
        empty = dataclasses.replace(self.layout, applications=[])
        result = osw.scan(empty)
        by_path = {i["path"]: i for i in result["items"]}
        self.assertFalse(by_path[self.fx["bundle_pref"]]["selected"])
        self.assertTrue(by_path[self.fx["hist_name"]]["selected"],
                        "历史证据独立于清单, 不降级")
        self.assertTrue(any("降级为默认不勾选" in w for w in result["warnings"]))

    def test_history_evidence_persistence(self):
        """换一份全新的历史(空), 历史层证据消失, Bundle 层仍在"""
        empty_dir = tempfile.mkdtemp(prefix="empty_hist_")
        self.addCleanup(shutil.rmtree, empty_dir, True)
        os.environ["DEEPCLEAN_HISTORY_PATH"] = os.path.join(empty_dir, "h.json")
        result = osw.scan(self.layout)
        paths = {i["path"] for i in result["items"]}
        self.assertIn(self.fx["bundle_pref"], paths)
        self.assertNotIn(self.fx["hist_name"], paths)
        self.assertNotIn(self.fx["file_cache"], paths)


class TestOrphanDelete(OrphanBase):

    def test_delete_valid_targets(self):
        targets = [self.fx["bundle_pref"], self.fx["bundle_receipt"],
                   self.fx["hist_name"], self.fx["hist_dotfile"]]
        out = osw.delete_orphans(targets, mode="permanent", layout=self.layout)
        self.assertEqual(out["status"], "success", out)
        self.assertEqual(out["removed_count"], 4)
        self.assertGreater(out["total_size"], 0)
        for t in targets:
            self.assertFalse(os.path.exists(t), f"未删除: {t}")
        # 无关文件完好
        self.assertTrue(os.path.exists(os.path.join(self.p["home"], "Documents", "notes.txt")))
        self.assertTrue(os.path.exists(self.fx["no_evidence"]))
        self.assertTrue(os.path.exists(self.fx["owned_foo"]))
        self.assertTrue(os.path.exists(self.fx["sys_pref"]))

    def test_all_or_nothing_on_illegal_target(self):
        good = self.fx["bundle_pref"]
        evil = os.path.join(self.p["home"], "Documents", "notes.txt")
        with self.assertRaises(au.UnsafePathError) as ctx:
            osw.delete_orphans([good, evil], mode="permanent", layout=self.layout)
        self.assertTrue(os.path.exists(good), "整体拒绝时好目标也不能被删")
        self.assertTrue(os.path.exists(evil))
        reasons = {r["path"]: r["reason"] for r in ctx.exception.rejected}
        self.assertIn("不在允许的残留目录范围内", reasons[evil])

    def test_reject_system_prefix_and_roots(self):
        for bad, keyword in (
            (self.fx["sys_pref"], "系统组件"),
            (os.path.join(self.p["lib"], "Caches"), "残留根"),
            (os.path.join(self.p["home"], "Documents"), "不在允许"),
        ):
            with self.assertRaises(au.UnsafePathError) as ctx:
                osw.delete_orphans([bad], mode="trash", layout=self.layout)
            self.assertIn(keyword, ctx.exception.rejected[0]["reason"], bad)
            self.assertTrue(os.path.exists(bad), f"被拒目标被动了: {bad}")

    def test_trash_mode_and_invalid_mode(self):
        out = osw.delete_orphans([self.fx["file_cache"]], mode="trash",
                                  layout=self.layout)
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["results"][0]["status"], "trashed")
        self.assertFalse(os.path.exists(self.fx["file_cache"]))
        with self.assertRaises(ValueError):
            osw.delete_orphans([self.fx["bundle_pref"]], mode="shred",
                               layout=self.layout)
        with self.assertRaises(ValueError):
            osw.delete_orphans([], mode="trash", layout=self.layout)


class TestOrphanAPI(OrphanBase):
    """FastAPI 接口层: 真实 uvicorn + httpx 全链路(扫描→清理→历史落账→统计)"""

    @classmethod
    def setUpClass(cls):
        cwd = os.getcwd()
        os.chdir(BACKEND_DIR)
        try:
            import api as api_module  # noqa
        finally:
            os.chdir(cwd)

        import httpx
        import socket
        import threading
        import uvicorn

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        config = uvicorn.Config(api_module.app, host="127.0.0.1",
                                port=port, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls.http = httpx.Client(base_url=f"http://127.0.0.1:{port}",
                                trust_env=False, timeout=30)
        for _ in range(150):
            if cls.server.started:
                break
            import time
            time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn 启动超时")

    @classmethod
    def tearDownClass(cls):
        cls.http.close()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    def setUp(self):
        super().setUp()
        au.set_layout(au.sandbox_layout(self.p["root"]))
        self.addCleanup(au.set_layout, None)

    def test_full_flow_scan_delete_history_stats(self):
        # ① 扫描
        resp = self.http.get("/api/orphans/scan")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertGreater(data["summary"]["count"], 0)
        paths = {i["path"] for i in data["items"]}
        self.assertIn(self.fx["bundle_pref"], paths)
        self.assertNotIn(self.fx["sys_pref"], paths)

        # ② 清理(带一个非法目标 → 整体拒绝 400, 什么都没删)
        evil = os.path.join(self.p["home"], "Documents", "notes.txt")
        resp = self.http.post("/api/orphans/delete", json={
            "paths": [self.fx["bundle_pref"], evil], "mode": "permanent"})
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("rejected", detail)
        self.assertTrue(os.path.exists(self.fx["bundle_pref"]))

        # ③ 合法清理 → 落账
        targets = [self.fx["bundle_pref"], self.fx["hist_name"], self.fx["file_cache"]]
        resp = self.http.post("/api/orphans/delete", json={
            "paths": targets, "mode": "permanent"})
        self.assertEqual(resp.status_code, 200, resp.text)
        out = resp.json()
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["removed_count"], 3)
        for t in targets:
            self.assertFalse(os.path.exists(t))

        # ④ 历史列表: 含 setUp 种入的 2 条 + 本次 1 条
        resp = self.http.get("/api/history")
        self.assertEqual(resp.status_code, 200)
        hist_data = resp.json()
        self.assertEqual(hist_data["total"], 3)
        latest = hist_data["records"][0]
        self.assertEqual(latest["source"], "orphan_residue")
        self.assertEqual(latest["count"], 3)
        self.assertEqual(latest["bytes"], out["total_size"])

        # 过滤
        resp = self.http.get("/api/history", params={"source": "app_uninstall"})
        self.assertEqual(resp.json()["total"], 1)

        # ⑤ 统计
        resp = self.http.get("/api/history/stats")
        st = resp.json()
        self.assertEqual(st["operations"], 3)
        self.assertEqual(st["by_source"]["orphan_residue"]["operations"], 1)
        self.assertGreater(st["bytes_freed"], 0)
        self.assertEqual(st["apps_uninstalled"], 1)
        self.assertGreaterEqual(st["residues_cleared"], 1)

        # ⑥ 清空历史
        resp = self.http.delete("/api/history")
        self.assertEqual(resp.json()["cleared"], 3)
        resp = self.http.get("/api/history/stats")
        self.assertEqual(resp.json()["operations"], 0)


if __name__ == "__main__":
    unittest.main()
