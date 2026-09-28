# _*_ coding: utf-8 -*-
"""app_uninstaller 单元测试 + API 集成测试

在沙盒中仿真一棵 macOS 目录树(/Applications、~/Library、/Library、
/private/var/db/receipts 等), 对"应用深度卸载"做端到端验证:
- 应用发现与 Info.plist(XML/二进制)解析
- 残留文件匹配(精确集合断言, 验证无误杀/无漏抓)
- 删除目标安全校验(路径穿越、符号链接逃逸、范围外路径)
- 真实执行卸载(废纸篓/永久删除)并验证无关文件完好
- FastAPI 接口层(列表/分析/卸载)行为

运行:  cd backend && python3 -m unittest tests.test_app_uninstaller -v
      (或直接 python3 tests/test_app_uninstaller.py)
"""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import app_uninstaller as au  # noqa: E402

SCRATCH = os.environ.get("TMPDIR") or tempfile.gettempdir()

BUNDLE_ID = "com.vendor.foo"
APP_NAME = "Foo"

# 沙盒中的绝对路径(在 build_sandbox 里赋值)
ROOT = HOME = LIB = None
FOO_APP = BAR_APP = BAZ_APP = NOPLIST_APP = None


def _write(path, data=b"data"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _write_plist(path, info, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    with open(path, "wb") as f:
        plistlib.dump(info, f, fmt=fmt)
    return path


def build_sandbox():
    """构建仿真 macOS 目录树, 返回 (layout, 关键路径 dict)"""
    global ROOT, HOME, LIB, FOO_APP, BAR_APP, BAZ_APP, NOPLIST_APP
    root = tempfile.mkdtemp(prefix="deepclean_sandbox_", dir=SCRATCH)
    ROOT = root
    HOME = os.path.join(root, "Users", "tester")
    LIB = os.path.join(HOME, "Library")

    # ---- 应用 ----
    FOO_APP = os.path.join(root, "Applications", "Foo.app")
    _write_plist(os.path.join(FOO_APP, "Contents", "Info.plist"), {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleShortVersionString": "2.1.0",
        "CFBundleName": "Foo",
        "CFBundleExecutable": "Foo",
    })
    _write(os.path.join(FOO_APP, "Contents", "MacOS", "Foo"), b"F" * 1000)
    _write(os.path.join(FOO_APP, "Contents", "Resources", "app.icns"), b"I" * 500)

    BAR_APP = os.path.join(root, "Applications", "Utilities", "Bar.app")  # 二级目录
    _write_plist(os.path.join(BAR_APP, "Contents", "Info.plist"), {
        "CFBundleIdentifier": "com.vendor.bar",
        "CFBundleShortVersionString": "1.4.2",
        "CFBundleName": "Bar",
    }, binary=True)  # 二进制 plist
    _write(os.path.join(BAR_APP, "Contents", "MacOS", "Bar"), b"B" * 300)

    BAZ_APP = os.path.join(root, "System", "Applications", "Baz.app")  # 受保护前缀
    _write_plist(os.path.join(BAZ_APP, "Contents", "Info.plist"), {
        "CFBundleIdentifier": "com.vendor.baz",
        "CFBundleShortVersionString": "10.0",
        "CFBundleName": "Baz",
    })

    NOPLIST_APP = os.path.join(root, "Applications", "NoPlist.app")  # 无 Info.plist
    _write(os.path.join(NOPLIST_APP, "Contents", "MacOS", "NoPlist"), b"N" * 100)

    # ---- Foo 的用户级残留 ----
    _write(os.path.join(LIB, "Preferences", "com.vendor.foo.plist"), b"pref")
    _write(os.path.join(LIB, "Preferences", "ByHost", "com.vendor.foo.9CDD-1234.plist"), b"byhost")
    _write(os.path.join(LIB, "Caches", "com.vendor.foo", "cache1.bin"), b"c" * 64)
    _write(os.path.join(LIB, "Application Support", "Foo", "data.db"), b"d" * 200)
    _write(os.path.join(LIB, "Containers", "com.vendor.foo", "container.db"), b"k" * 32)
    _write(os.path.join(LIB, "Group Containers", "group.com.vendor.foo", "g.db"), b"g" * 32)
    _write(os.path.join(LIB, "HTTPStorages", "com.vendor.foo", "cache.db"), b"h" * 32)
    _write(os.path.join(LIB, "HTTPStorages", "com.vendor.foo.binarycookies"), b"ck")
    _write(os.path.join(LIB, "Saved Application State", "com.vendor.foo.savedState", "s.dat"), b"s" * 16)
    _write(os.path.join(LIB, "Cookies", "com.vendor.foo.binarycookies"), b"ck2")
    _write(os.path.join(LIB, "Logs", "Foo.log"), b"log")
    _write(os.path.join(LIB, "Logs", "DiagnosticReports", "Foo_2026-01-01.ips"), b"rep")
    _write(os.path.join(LIB, "LaunchAgents", "com.vendor.foo.plist"), b"agent")
    _write(os.path.join(LIB, "WebKit", "com.vendor.foo", "wk.db"), b"w" * 16)
    _write(os.path.join(HOME, ".Foo", "config.ini"), b"cfg")  # 隐藏配置

    # ---- Foo 的系统级残留 + 安装收据 ----
    _write(os.path.join(root, "private", "var", "db", "receipts", "com.vendor.foo.bom"), b"bom")
    _write(os.path.join(root, "private", "var", "db", "receipts", "com.vendor.foo.plist"), b"rct")

    # ---- Bar 的系统级残留 ----
    _write(os.path.join(root, "Library", "LaunchDaemons", "com.vendor.bar.plist"), b"daemon")
    _write(os.path.join(root, "Library", "Application Support", "Bar", "bar.conf"), b"bconf")

    # ---- 诱饵: 名称相近但绝不能被匹配/删除 ----
    _write(os.path.join(LIB, "Preferences", "com.vendor.foo2.plist"), b"decoy1")
    _write(os.path.join(LIB, "Caches", "com.vendor.foobar", "x.bin"), b"decoy2")
    _write(os.path.join(LIB, "Application Support", "FooBar", "keep.txt"), b"decoy3")
    _write(os.path.join(LIB, "Application Support", "com.vendor.fooextra", "keep2.txt"), b"decoy4")
    _write(os.path.join(HOME, ".Keep", "keep.dat"), b"decoy5")
    _write(os.path.join(root, "private", "var", "db", "receipts", "com.vendor.foobar.bom"), b"decoy6")
    _write(os.path.join(HOME, "Documents", "notes.txt"), b"decoy7")

    os.makedirs(os.path.join(HOME, ".Trash"), exist_ok=True)
    layout = au.sandbox_layout(root)
    return layout, {
        "root": root, "home": HOME, "lib": LIB,
        "foo": FOO_APP, "bar": BAR_APP, "baz": BAZ_APP, "nopl": NOPLIST_APP,
    }


# Foo 应分析出的全部残留(不含诱饵), 由 foo_expected_paths 基于沙盒路径构造
def foo_expected_paths(lib, home, root):
    rels = [
        os.path.join("Preferences", "com.vendor.foo.plist"),
        os.path.join("Preferences", "ByHost", "com.vendor.foo.9CDD-1234.plist"),
        os.path.join("Caches", "com.vendor.foo"),
        os.path.join("Application Support", "Foo"),
        os.path.join("Containers", "com.vendor.foo"),
        os.path.join("Group Containers", "group.com.vendor.foo"),
        os.path.join("HTTPStorages", "com.vendor.foo"),
        os.path.join("HTTPStorages", "com.vendor.foo.binarycookies"),
        os.path.join("Saved Application State", "com.vendor.foo.savedState"),
        os.path.join("Cookies", "com.vendor.foo.binarycookies"),
        os.path.join("Logs", "Foo.log"),
        os.path.join("Logs", "DiagnosticReports", "Foo_2026-01-01.ips"),
        os.path.join("LaunchAgents", "com.vendor.foo.plist"),
        os.path.join("WebKit", "com.vendor.foo"),
    ]
    paths = {os.path.join(lib, r) for r in rels}
    paths.add(os.path.join(home, ".Foo"))
    paths.add(os.path.join(root, "private", "var", "db", "receipts", "com.vendor.foo.bom"))
    paths.add(os.path.join(root, "private", "var", "db", "receipts", "com.vendor.foo.plist"))
    return paths


DECOY_KEYS = [
    ("Preferences", "com.vendor.foo2.plist"),
    ("Caches", "com.vendor.foobar"),
    ("Application Support", "FooBar"),
    ("Application Support", "com.vendor.fooextra"),
]


class SandboxBase(unittest.TestCase):
    """每个用例都新建独立沙盒, 结束后彻底清理"""

    def setUp(self):
        self.layout, self.p = build_sandbox()
        self.addCleanup(self._teardown)

    def _teardown(self):
        shutil.rmtree(self.p["root"], ignore_errors=True)

    def assert_decoys_intact(self):
        lib, home, root = self.p["lib"], self.p["home"], self.p["root"]
        for rel_dir, name in DECOY_KEYS:
            self.assertTrue(os.path.exists(os.path.join(lib, rel_dir, name)),
                            f"诱饵被误删: {rel_dir}/{name}")
        self.assertTrue(os.path.exists(os.path.join(home, ".Keep")))
        self.assertTrue(os.path.exists(os.path.join(home, "Documents", "notes.txt")))
        self.assertTrue(os.path.exists(os.path.join(
            root, "private", "var", "db", "receipts", "com.vendor.foobar.bom")))


# ==================== 应用发现 ====================

class TestListApps(SandboxBase):

    def test_finds_bundles_and_metadata(self):
        apps = au.list_apps(layout=self.layout)
        self.assertEqual(len(apps), 4, [a["path"] for a in apps])
        by_name = {a["name"]: a for a in apps}

        foo = by_name["Foo"]
        self.assertEqual(foo["bundle_id"], BUNDLE_ID)
        self.assertEqual(foo["version"], "2.1.0")
        self.assertFalse(foo["system"])
        self.assertFalse(foo["running"])
        # 应用体积 = 三个组成文件之和
        expected_size = sum(os.path.getsize(p) for p in [
            os.path.join(foo["path"], "Contents", "Info.plist"),
            os.path.join(foo["path"], "Contents", "MacOS", "Foo"),
            os.path.join(foo["path"], "Contents", "Resources", "app.icns"),
        ])
        self.assertEqual(foo["size"], expected_size)

        # 二级目录 + 二进制 plist
        bar = by_name["Bar"]
        self.assertTrue(bar["path"].endswith(os.path.join("Utilities", "Bar.app")))
        self.assertEqual(bar["bundle_id"], "com.vendor.bar")
        self.assertEqual(bar["version"], "1.4.2")

        # 受保护前缀
        self.assertTrue(by_name["Baz"]["system"])

        # 缺 Info.plist: 用目录名兜底
        nopl = by_name["NoPlist"]
        self.assertEqual(nopl["bundle_id"], "")
        self.assertEqual(nopl["name"], "NoPlist")

    def test_query_filter_and_without_sizes(self):
        apps = au.list_apps(q="vendor.bar", layout=self.layout)
        self.assertEqual([a["name"] for a in apps], ["Bar"])

        apps = au.list_apps(q="foo", layout=self.layout)
        self.assertEqual([a["name"] for a in apps], ["Foo"])

        apps = au.list_apps(with_sizes=False, layout=self.layout)
        self.assertEqual(len(apps), 4)
        self.assertTrue(all(a["size"] is None for a in apps))


# ==================== 残留分析 ====================

class TestAnalyze(SandboxBase):

    def test_exact_residue_set(self):
        result = au.analyze_app(self.p["foo"], layout=self.layout)
        expected = foo_expected_paths(self.p["lib"], self.p["home"], self.p["root"])
        expected.add(self.p["foo"])  # 应用本体
        actual = {i["path"] for i in result["items"]}

        self.assertEqual(actual, expected,
                         f"多出: {actual - expected}; 缺少: {expected - actual}")
        self.assertEqual(len(result["items"]), len(expected))
        self.assertEqual(result["total_size"], sum(i["size"] for i in result["items"]))
        # 按体积降序, 应用本体最大应排第一
        self.assertEqual(result["items"][0]["path"], self.p["foo"])
        self.assertEqual(result["items"][0]["category"], au.CAT_APP)
        self.assertFalse(result["running"])
        self.assertEqual(result["pids"], [])

        cats = {i["path"]: i["category"] for i in result["items"]}
        lib = self.p["lib"]
        self.assertEqual(cats[os.path.join(lib, "Preferences", "com.vendor.foo.plist")], au.CAT_PREF)
        self.assertEqual(cats[os.path.join(lib, "Caches", "com.vendor.foo")], au.CAT_CACHE)
        self.assertEqual(cats[os.path.join(lib, "Containers", "com.vendor.foo")], au.CAT_CONTAINER)
        self.assertEqual(cats[os.path.join(lib, "Group Containers", "group.com.vendor.foo")], au.CAT_GROUP)
        self.assertEqual(cats[os.path.join(lib, "Logs", "Foo.log")], au.CAT_LOG)
        self.assertEqual(cats[os.path.join(lib, "LaunchAgents", "com.vendor.foo.plist")], au.CAT_LAUNCH)
        self.assertEqual(cats[os.path.join(self.p["home"], ".Foo")], au.CAT_DOTFILE)
        self.assertEqual(cats[os.path.join(
            self.p["root"], "private", "var", "db", "receipts", "com.vendor.foo.bom")], au.CAT_RECEIPT)

    def test_no_false_positive_short_name(self):
        """短应用名不做前缀匹配; 诱饵目录不得出现"""
        result = au.analyze_app(self.p["foo"], layout=self.layout)
        paths = {i["path"] for i in result["items"]}
        lib = self.p["lib"]
        for rel_dir, name in DECOY_KEYS:
            self.assertNotIn(os.path.join(lib, rel_dir, name), paths)
        self.assertNotIn(os.path.join(self.p["home"], ".Keep"), paths)
        self.assertNotIn(os.path.join(
            self.p["root"], "private", "var", "db", "receipts", "com.vendor.foobar.bom"), paths)

    def test_bar_system_residue_and_admin_flag(self):
        result = au.analyze_app(self.p["bar"], layout=self.layout)
        paths = {i["path"] for i in result["items"]}
        daemon = os.path.join(self.p["root"], "Library", "LaunchDaemons", "com.vendor.bar.plist")
        support = os.path.join(self.p["root"], "Library", "Application Support", "Bar")
        self.assertIn(daemon, paths)
        self.assertIn(support, paths)
        by_path = {i["path"]: i for i in result["items"]}
        self.assertTrue(by_path[daemon]["needs_admin"])
        self.assertEqual(by_path[daemon]["scope"], "system")
        self.assertEqual(by_path[self.p["bar"]]["scope"], "user")

    def test_noplist_analyze_by_name(self):
        result = au.analyze_app(self.p["nopl"], layout=self.layout)
        self.assertEqual(result["app"]["bundle_id"], "")
        self.assertEqual(len(result["items"]), 1)  # 无 bundle id 也无同名残留 → 只有本体
        self.assertTrue(any("仅按应用名匹配" in w for w in result["warnings"]))

    def test_reject_paths_outside_applications(self):
        for bad in ["/tmp/Evil.app",
                    os.path.join(self.p["home"], "Documents", "Evil.app"),
                    "/etc/passwd"]:
            with self.assertRaises(au.InvalidAppPathError, msg=bad):
                au.analyze_app(bad, layout=self.layout)
        with self.assertRaises(FileNotFoundError):
            au.analyze_app(os.path.join(self.p["root"], "Applications", "Ghost.app"),
                           layout=self.layout)


# ==================== 安全校验 ====================

class TestValidateTarget(SandboxBase):

    def ok(self, path, **kw):
        good, reason = au.validate_target(self.layout, path, **kw)
        self.assertTrue(good, f"{path} 应通过校验, 实际: {reason}")
        return reason

    def bad(self, path, **kw):
        good, reason = au.validate_target(self.layout, path, **kw)
        self.assertFalse(good, f"{path} 应被拒绝")
        return reason

    def test_accept_legit_targets(self):
        lib, home = self.p["lib"], self.p["home"]
        kw = {"app_path": self.p["foo"], "bundle_id": BUNDLE_ID, "app_name": APP_NAME}
        self.assertEqual(self.ok(self.p["foo"], **kw), "应用本体")
        self.ok(os.path.join(lib, "Preferences", "com.vendor.foo.plist"), **kw)
        self.ok(os.path.join(lib, "Application Support", "Foo"), **kw)
        self.ok(os.path.join(home, ".Foo"), **kw)
        self.ok(os.path.join(self.p["root"], "private", "var", "db", "receipts",
                             "com.vendor.foo.bom"), **kw)

    def test_reject_scope_escapes(self):
        lib, home = self.p["lib"], self.p["home"]
        kw = {"app_path": self.p["foo"], "bundle_id": BUNDLE_ID, "app_name": APP_NAME}
        self.assertEqual(self.bad("/etc/passwd", **kw), "不在允许的残留目录范围内")
        self.assertEqual(self.bad("/", **kw), "不能删除根目录")
        self.assertEqual(self.bad(home), "不能删除用户主目录")
        # 名称匹配但不在允许的残留根内
        self.assertEqual(self.bad(os.path.join(home, "Documents", "com.vendor.foo.plist"), **kw),
                         "不在允许的残留目录范围内")
        # 在允许根内但名称与该应用无关
        self.assertEqual(self.bad(os.path.join(lib, "Caches", "com.vendor.foo2.plist"), **kw),
                         "名称与该应用不匹配")
        self.assertEqual(self.bad(os.path.join(home, ".Keep"), **kw),
                         "名称与该应用不匹配")
        # 收据目录必须 Bundle ID 匹配
        self.assertEqual(self.bad(os.path.join(
            self.p["root"], "private", "var", "db", "receipts", "com.vendor.foobar.bom"), **kw),
            "安装收据目录仅允许按 Bundle ID 匹配")
        # 相对路径穿越
        self.bad(os.path.join(home, "Library", "Caches", "..", "..", "Documents", "notes.txt"), **kw)
        # 应用路径不合法时, 应用本体也不许删
        evil_kw = {"app_path": "/tmp/Evil.app", "bundle_id": BUNDLE_ID, "app_name": APP_NAME}
        self.assertEqual(self.bad("/tmp/Evil.app", **evil_kw), "应用路径不在允许的应用目录内")

    def test_reject_symlink_escape(self):
        """残留条目若是指向允许范围外的符号链接, 分析可见但删除必须拒绝"""
        lib = self.p["lib"]
        link = os.path.join(lib, "Caches", "com.vendor.foo.link")
        os.symlink("/etc", link)

        result = au.analyze_app(self.p["foo"], layout=self.layout)
        self.assertIn(link, {i["path"] for i in result["items"]}, "符号链接条目应被分析到")

        kw = {"app_path": self.p["foo"], "bundle_id": BUNDLE_ID, "app_name": APP_NAME}
        self.assertEqual(self.bad(link, **kw), "经由符号链接指向允许范围之外")

        with self.assertRaises(au.UnsafePathError) as ctx:
            au.uninstall_app(self.p["foo"], [self.p["foo"], link],
                             bundle_id=BUNDLE_ID, app_name=APP_NAME,
                             layout=self.layout)
        self.assertTrue(os.path.exists(self.p["foo"]), "整体拒绝后应用本体必须完好")
        self.assertTrue(os.path.islink(link))


# ==================== 执行卸载 ====================

class TestUninstall(SandboxBase):

    def _all_targets(self):
        result = au.analyze_app(self.p["foo"], layout=self.layout)
        return [i["path"] for i in result["items"]]

    def test_trash_mode_moves_all_and_stops_process(self):
        # 启动一个"运行中的应用"进程(命令行携带应用路径)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", self.p["foo"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        time.sleep(0.3)

        targets = self._all_targets()
        out = au.uninstall_app(self.p["foo"], targets, bundle_id=BUNDLE_ID,
                               app_name=APP_NAME, mode="trash",
                               stop_processes=True, layout=self.layout)

        self.assertEqual(out["status"], "success", out)
        self.assertEqual(out["removed_count"], len(targets))
        self.assertEqual(out["failed_count"], 0)

        # 进程已被结束
        self.assertGreaterEqual(len(out["stopped"]), 1)
        proc.wait(timeout=10)
        self.assertIsNotNone(proc.poll())

        # 全部移入废纸篓, 原位置消失
        trash = os.path.join(self.p["home"], ".Trash")
        for t in targets:
            self.assertFalse(os.path.lexists(t), f"原路径未删除: {t}")
            self.assertTrue(os.path.exists(os.path.join(trash, os.path.basename(t))),
                            f"废纸篓中缺少: {os.path.basename(t)}")

        self.assert_decoys_intact()

    def test_permanent_mode(self):
        targets = self._all_targets()
        out = au.uninstall_app(self.p["foo"], targets, bundle_id=BUNDLE_ID,
                               app_name=APP_NAME, mode="permanent",
                               stop_processes=False, forget_receipts=True,
                               layout=self.layout)
        self.assertEqual(out["status"], "success", out)
        self.assertEqual(out["removed_count"], len(targets))
        for t in targets:
            self.assertFalse(os.path.lexists(t), f"未删除: {t}")
        # 永久删除模式不应往废纸篓塞东西; 非 macOS 不执行 pkgutil
        trash = os.path.join(self.p["home"], ".Trash")
        self.assertEqual(os.listdir(trash), [])
        self.assertFalse(out["receipt_forgotten"])
        self.assert_decoys_intact()

    def test_all_or_nothing_on_invalid_target(self):
        targets = self._all_targets()
        notes = os.path.join(self.p["home"], "Documents", "notes.txt")
        with self.assertRaises(au.UnsafePathError) as ctx:
            au.uninstall_app(self.p["foo"], targets + [notes], bundle_id=BUNDLE_ID,
                             app_name=APP_NAME, mode="permanent", layout=self.layout)
        rejected = ctx.exception.rejected
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["path"], notes)
        # 一个都没删
        self.assertTrue(os.path.exists(self.p["foo"]))
        self.assertTrue(os.path.exists(notes))
        self.assertTrue(os.path.exists(os.path.join(
            self.p["lib"], "Preferences", "com.vendor.foo.plist")))

    def test_argument_validation(self):
        with self.assertRaises(ValueError):
            au.uninstall_app(self.p["foo"], [self.p["foo"]], mode="shred", layout=self.layout)
        with self.assertRaises(ValueError):
            au.uninstall_app(self.p["foo"], [], mode="trash", layout=self.layout)
        with self.assertRaises(au.InvalidAppPathError):
            au.uninstall_app("/tmp/Evil.app", ["/tmp/Evil.app"], mode="trash",
                             layout=self.layout)

    def test_dedupe_and_missing_path_skipped(self):
        gone = os.path.join(self.p["lib"], "Caches", "com.vendor.foo.gone.plist")
        out = au.uninstall_app(self.p["foo"], [self.p["foo"], self.p["foo"], gone],
                               bundle_id=BUNDLE_ID, app_name=APP_NAME,
                               mode="trash", stop_processes=False, layout=self.layout)
        self.assertEqual(len(out["results"]), 2)  # 去重
        statuses = {r["path"]: r["status"] for r in out["results"]}
        self.assertEqual(statuses[self.p["foo"]], "trashed")
        self.assertEqual(statuses[gone], "skipped")

    def test_stop_app_processes_only(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", self.p["foo"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        time.sleep(0.3)
        out = au.stop_app_processes(self.p["foo"], self.layout)
        self.assertGreaterEqual(len(out), 1)
        proc.wait(timeout=10)
        self.assertIsNotNone(proc.poll())


# ==================== API 集成测试 ====================

class TestAppsAPI(SandboxBase):
    """FastAPI 接口层: 真实起 uvicorn 服务, 用 httpx 走一遍 HTTP 链路"""

    @classmethod
    def setUpClass(cls):
        # logger.init 会以 CWD 建 logs 目录 → 先切到 backend 目录再 import api
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

        config = uvicorn.Config(api_module.app, host="127.0.0.1", port=port, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{port}"
        # trust_env=False: 不受系统 HTTP_PROXY/Clash 环境变量影响
        cls.http = httpx.Client(base_url=cls.base_url, trust_env=False, timeout=30)

        for _ in range(150):
            if cls.server.started:
                break
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

    def test_list_endpoint(self):
        resp = self.http.get("/api/apps")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["total"], 4)
        self.assertFalse(data["is_macos"])
        self.assertEqual(sorted(a["name"] for a in data["apps"]),
                         ["Bar", "Baz", "Foo", "NoPlist"])

        resp = self.http.get("/api/apps", params={"q": "foo"})
        self.assertEqual(resp.json()["total"], 1)

    def test_analyze_endpoint(self):
        resp = self.http.get("/api/apps/analyze", params={"path": self.p["foo"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        expected = foo_expected_paths(self.p["lib"], self.p["home"], self.p["root"])
        expected.add(self.p["foo"])
        self.assertEqual({i["path"] for i in data["items"]}, expected)

        resp = self.http.get("/api/apps/analyze", params={"path": "/tmp/Evil.app"})
        self.assertEqual(resp.status_code, 400)
        resp = self.http.get("/api/apps/analyze",
                             params={"path": os.path.join(self.p["root"], "Applications", "Nope.app")})
        self.assertEqual(resp.status_code, 404)

    def test_uninstall_endpoint_rejects_bad_target(self):
        notes = os.path.join(self.p["home"], "Documents", "notes.txt")
        resp = self.http.post("/api/apps/uninstall", json={
            "app_path": self.p["foo"],
            "paths": [self.p["foo"], notes],
            "bundle_id": BUNDLE_ID, "app_name": APP_NAME, "mode": "permanent",
        })
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("整体拒绝", detail["message"])
        self.assertEqual(detail["rejected"][0]["path"], notes)
        self.assertTrue(os.path.exists(self.p["foo"]))
        self.assertTrue(os.path.exists(notes))

    def test_uninstall_endpoint_success(self):
        resp = self.http.get("/api/apps/analyze", params={"path": self.p["foo"]})
        items = resp.json()["items"]

        resp = self.http.post("/api/apps/uninstall", json={
            "app_path": self.p["foo"],
            "paths": [i["path"] for i in items],
            "bundle_id": BUNDLE_ID, "app_name": APP_NAME, "mode": "permanent",
            "stop_processes": False, "forget_receipts": False,
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "success", data)
        self.assertEqual(data["removed_count"], len(items))
        for i in items:
            self.assertFalse(os.path.lexists(i["path"]))
        self.assert_decoys_intact()

    def test_uninstall_endpoint_bad_mode(self):
        resp = self.http.post("/api/apps/uninstall", json={
            "app_path": self.p["foo"], "paths": [self.p["foo"]], "mode": "shred",
        })
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
