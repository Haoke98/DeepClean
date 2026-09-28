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
FOO_APP = BAR_APP = BAZ_APP = NOPLIST_APP = ZZ_APP = None


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
    global ROOT, HOME, LIB, FOO_APP, BAR_APP, BAZ_APP, NOPLIST_APP, ZZ_APP
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

    # 带中文本地化名的应用(旧式 key = "value"; 文本格式的 InfoPlist.strings)
    ZZ_APP = os.path.join(root, "Applications", "ZZTool.app")
    _write_plist(os.path.join(ZZ_APP, "Contents", "Info.plist"), {
        "CFBundleIdentifier": "cn.zz.tool",
        "CFBundleShortVersionString": "5.0.0",
        "CFBundleName": "ZZTool",
    })
    _write(os.path.join(ZZ_APP, "Contents", "MacOS", "ZZTool"), b"Z" * 800)
    _write(os.path.join(ZZ_APP, "Contents", "Resources", "zh_CN.lproj", "InfoPlist.strings"),
           b'/* localized names */\nCFBundleDisplayName = "\xe6\xb5\x8b\xe8\xaf\x95\xe5\x8a\xa9\xe6\x89\x8b";\n')

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

    # ---- ZZTool 的残留: 强关联(含新增扫描根) ----
    _write(os.path.join(LIB, "Preferences", "cn.zz.tool.plist"), b"zzpref")
    _write(os.path.join(LIB, "Application Support", "CrashReporter", "ZZTool_61A1.plist"), b"zzcrash")
    _write(os.path.join(root, "private", "var", "folders", "3v", "lbc5xyz", "C",
                        "cn.zz.tool", "cache.bin"), b"z" * 64)
    # 模糊(纯包含)才能命中的: 用户数据/安装包 —— 应列出但默认不勾选
    _write(os.path.join(HOME, "Downloads", "ZZToolDownloads", "pkg.ipa"), b"d" * 128)
    _write(os.path.join(os.path.dirname(HOME), "Shared", "PKGgs",
                        "ZZTool_v1_arm64.dmg"), b"m" * 256)

    # ---- 诱饵: 名称相近但绝不能被匹配/删除 ----
    _write(os.path.join(LIB, "Preferences", "com.vendor.foo2.plist"), b"decoy1")
    _write(os.path.join(LIB, "Caches", "com.vendor.foobar", "x.bin"), b"decoy2")
    _write(os.path.join(LIB, "Application Support", "FooBar", "keep.txt"), b"decoy3")
    _write(os.path.join(LIB, "Application Support", "com.vendor.fooextra", "keep2.txt"), b"decoy4")
    _write(os.path.join(HOME, ".Keep", "keep.dat"), b"decoy5")
    _write(os.path.join(root, "private", "var", "db", "receipts", "com.vendor.foobar.bom"), b"decoy6")
    _write(os.path.join(HOME, "Documents", "notes.txt"), b"decoy7")
    _write(os.path.join(HOME, "Downloads", "KeepMe.dmg"), b"decoy8")
    _write(os.path.join(os.path.dirname(HOME), "Shared", "PKGgs", "Other_v1.dmg"), b"decoy9")

    os.makedirs(os.path.join(HOME, ".Trash"), exist_ok=True)
    layout = au.sandbox_layout(root)
    return layout, {
        "root": root, "home": HOME, "lib": LIB,
        "foo": FOO_APP, "bar": BAR_APP, "baz": BAZ_APP, "nopl": NOPLIST_APP,
        "zz": ZZ_APP,
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
        self.assertTrue(os.path.exists(os.path.join(home, "Downloads", "KeepMe.dmg")))
        self.assertTrue(os.path.exists(os.path.join(
            os.path.dirname(home), "Shared", "PKGgs", "Other_v1.dmg")))


# ==================== 应用发现 ====================

class TestListApps(SandboxBase):

    def test_finds_bundles_and_metadata(self):
        apps = au.list_apps(layout=self.layout)
        self.assertEqual(len(apps), 5, [a["path"] for a in apps])
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
        self.assertEqual(len(apps), 5)
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
        # 第一层已命中的目录整体列出即可, 其子文件不再重复单列
        self.assertNotIn(os.path.join(support, "bar.conf"), paths)
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




# ==================== 排序 / 本地化搜索 / 两层匹配 ====================

class TestSortAndSearch(SandboxBase):
    """按大小排序、按中文本地化名/别名搜索"""

    def setUp(self):
        super().setUp()
        # 固定系统语言: 让 zh_CN.lproj 的本地化名成为显示名(Finder 中文环境)
        os.environ["LANGUAGE"] = "zh_CN.UTF-8"
        self.addCleanup(os.environ.pop, "LANGUAGE", None)

    def test_default_sort_by_size_desc(self):
        apps = au.list_apps(layout=self.layout)
        sizes = [a["size"] for a in apps]
        self.assertEqual(sizes, sorted(sizes, reverse=True), sizes)

    def test_sort_by_name_and_order_params(self):
        asc = au.list_apps(sort_by="name", order="asc", layout=self.layout)
        names = [a["name"] for a in asc]
        self.assertEqual(names, sorted(names, key=str.lower))

        desc = au.list_apps(sort_by="name", order="desc", layout=self.layout)
        self.assertEqual([a["name"] for a in desc], sorted(names, key=str.lower, reverse=True))

        size_desc = au.list_apps(sort_by="size", order="desc", layout=self.layout)
        self.assertEqual([a["size"] for a in size_desc],
                         sorted((a["size"] for a in size_desc), reverse=True))

        # 非法参数回退到默认(size 降序)
        fallback = au.list_apps(sort_by="hack", order="up", layout=self.layout)
        self.assertEqual([a["size"] for a in fallback],
                         sorted((a["size"] for a in fallback), reverse=True))

    def test_search_by_localized_display_name(self):
        zz = [a for a in au.list_apps(layout=self.layout) if a["path"] == self.p["zz"]][0]
        # 系统语言为中文时, 显示名 = InfoPlist.strings 里的本地化名
        self.assertEqual(zz["name"], "\u6d4b\u8bd5\u52a9\u624b")
        self.assertIn("ZZTool", zz["aliases"])
        # 通过 Finder 显示名搜索
        apps = au.list_apps(q="\u6d4b\u8bd5\u52a9\u624b", layout=self.layout)
        self.assertEqual([a["path"] for a in apps], [self.p["zz"]])
        # 通过目录原名 / Bundle ID 搜索
        self.assertEqual([a["path"] for a in au.list_apps(q="zztool", layout=self.layout)],
                         [self.p["zz"]])
        self.assertEqual([a["path"] for a in au.list_apps(q="cn.zz.tool", layout=self.layout)],
                         [self.p["zz"]])
        # 不匹配的应用不被带出
        self.assertEqual(au.list_apps(q="zztool", layout=self.layout).__len__(), 1)


class TestFuzzyTier(SandboxBase):
    """残留匹配分层: 强关联默认勾选 / 关键字模糊疑似默认不勾选 + 新扫描根"""

    def setUp(self):
        super().setUp()
        os.environ["LANGUAGE"] = "zh_CN.UTF-8"
        self.addCleanup(os.environ.pop, "LANGUAGE", None)

    def test_residue_tiers_and_new_roots(self):
        result = au.analyze_app(self.p["zz"], layout=self.layout)
        by_path = {i["path"]: i for i in result["items"]}

        strong = [
            self.p["zz"],                                                    # 应用本体
            os.path.join(LIB, "Preferences", "cn.zz.tool.plist"),            # 强关联
            os.path.join(LIB, "Application Support", "CrashReporter",
                         "ZZTool_61A1.plist"),                               # 二级目录
            os.path.join(ROOT, "private", "var", "folders", "3v", "lbc5xyz",
                         "C", "cn.zz.tool"),                                 # 系统临时缓存
        ]
        for p in strong:
            self.assertIn(p, by_path, f"缺少: {p}")
            self.assertTrue(by_path[p]["selected"], f"强关联应默认勾选: {p}")
            self.assertNotEqual(by_path[p]["match"], "fuzzy", p)

        # 模糊(纯包含)命中: ZZTool + D 不构成名称边界 → fuzzy, 默认不勾选
        dl = os.path.join(HOME, "Downloads", "ZZToolDownloads")
        self.assertIn(dl, by_path)
        self.assertEqual(by_path[dl]["match"], "fuzzy")
        self.assertFalse(by_path[dl]["selected"])

        # 安装包: ZZTool_ 构成名称边界(name 命中), 但分类属 NOAUTO → 默认不勾选
        pkg = os.path.join(os.path.dirname(HOME), "Shared", "PKGgs", "ZZTool_v1_arm64.dmg")
        self.assertIn(pkg, by_path)
        self.assertEqual(by_path[pkg]["category"], au.CAT_INSTALLER)
        self.assertFalse(by_path[pkg]["selected"])
        fuzzy = [dl, pkg]

        # 分类归属
        self.assertEqual(by_path[os.path.join(HOME, "Downloads", "ZZToolDownloads")]["category"],
                         au.CAT_DOWNLOAD)
        self.assertEqual(by_path[os.path.join(os.path.dirname(HOME), "Shared", "PKGgs",
                                              "ZZTool_v1_arm64.dmg")]["category"],
                         au.CAT_INSTALLER)
        # 提示语
        self.assertTrue(any("\u7591\u4f3c" in w for w in result["warnings"]), result["warnings"])
        # 强关联(默认勾选)整体排在疑似项之前
        flags = [i["selected"] for i in result["items"]]
        self.assertEqual(flags, sorted(flags, reverse=True), "疑似项应排在强关联之后")
        # 诱饵绝不出现(另一应用/无关文件)
        self.assertNotIn(os.path.join(HOME, "Downloads", "KeepMe.dmg"), by_path)
        self.assertNotIn(os.path.join(os.path.dirname(HOME), "Shared", "PKGgs",
                                      "Other_v1.dmg"), by_path)

    def test_fuzzy_paths_validate_only_inside_roots(self):
        kw = {"app_path": self.p["zz"], "bundle_id": "cn.zz.tool", "app_name": "ZZTool"}
        dl = os.path.join(HOME, "Downloads", "ZZToolDownloads")
        good, reason = au.validate_target(self.layout, dl, **kw)
        self.assertTrue(good, reason)
        self.assertIn("\u6a21\u7cca", reason)

        # 同在 Downloads 根内但与该应用无关 → 拒绝
        self.assertFalse(au.validate_target(
            self.layout, os.path.join(HOME, "Downloads", "KeepMe.dmg"), **kw)[0])
        # 根外即使名称命中也拒绝
        notes = os.path.join(HOME, "Documents", "ZZTool_notes.txt")
        _write(notes, b"x")
        self.assertFalse(au.validate_target(self.layout, notes, **kw)[0])

    def test_uninstall_with_fuzzy_selection(self):
        result = au.analyze_app(self.p["zz"], layout=self.layout)
        paths = [i["path"] for i in result["items"] if i["selected"]]   # 默认勾选(强关联)
        fuzzy_path = os.path.join(HOME, "Downloads", "ZZToolDownloads")
        paths.append(fuzzy_path)                                        # 用户手动勾选疑似项

        out = au.uninstall_app(self.p["zz"], paths, bundle_id="cn.zz.tool",
                               app_name="ZZTool", mode="trash",
                               stop_processes=False, layout=self.layout)
        self.assertEqual(out["status"], "success", out)
        self.assertEqual(out["removed_count"], len(paths))
        for p in paths:
            self.assertFalse(os.path.lexists(p), f"未删除: {p}")
        self.assert_decoys_intact()
        # 没勾选的疑似项(安装包)留在原地
        self.assertTrue(os.path.exists(
            os.path.join(os.path.dirname(HOME), "Shared", "PKGgs", "ZZTool_v1_arm64.dmg")))

    def test_bar_analysis_has_no_fuzzy_noise(self):
        """通用分段(>=7字)门槛: 分析 Bar 不得把别的应用目录列为疑似"""
        result = au.analyze_app(self.p["bar"], layout=self.layout)
        fuzzy = [i for i in result["items"] if i["match"] == "fuzzy"]
        self.assertEqual(fuzzy, [], fuzzy)


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
        self.assertEqual(data["total"], 5)
        self.assertFalse(data["is_macos"])
        self.assertEqual(sorted(a["name"] for a in data["apps"]),
                         ["Bar", "Baz", "Foo", "NoPlist", "ZZTool"])

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


    def test_list_sort_and_alias_query(self):
        # 默认按体积降序
        resp = self.http.get("/api/apps")
        sizes = [a["size"] for a in resp.json()["apps"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        # 按名称升序
        resp = self.http.get("/api/apps", params={"sort_by": "name", "order": "asc"})
        names = [a["name"] for a in resp.json()["apps"]]
        self.assertEqual(names, sorted(names, key=str.lower))
        # 中文本地化名可搜
        resp = self.http.get("/api/apps", params={"q": "测试助手"})
        self.assertEqual(resp.json()["total"], 1)

    def test_uninstall_endpoint_bad_mode(self):
        resp = self.http.post("/api/apps/uninstall", json={
            "app_path": self.p["foo"], "paths": [self.p["foo"]], "mode": "shred",
        })
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
