# _*_ coding: utf-8 -*-
"""
macOS 应用深度卸载 (App Deep Uninstall)

功能:
    1. 列出 /Applications、~/Applications、/System/Applications 下的 .app 应用
       (读取 Contents/Info.plist 获取 Bundle ID / 版本, 统计应用体积, 检测运行状态);
    2. 按 Bundle ID / 应用名(含 InfoPlist.strings 本地化别名)在各残留目录中
       定位关联文件: 偏好设置、缓存、应用数据(含 CrashReporter 等二级目录)、
       沙盒容器、群组容器、网络缓存、窗口状态、Cookie、日志、崩溃报告、启动项、
       特权助手、安装收据(pkgutil)、Downloads 数据、安装包、系统临时缓存
       (/private/var/folders) 以及 ~/.<AppName> 隐藏配置;
       匹配分两层: 强关联(边界匹配, 默认勾选)与关键字模糊疑似(默认不勾选);
    3. 一键深度卸载: 可先退出运行中的进程, 再移入废纸篓(默认)或永久删除,
       系统目录(/Library、安装收据)自动提权, 并可 pkgutil --forget 忘记收据。

设计要点:
    - MacLayout 布局对象可注入: 通过 DEEPCLEAN_APP_UNINSTALL_SANDBOX 环境变量
      或 set_layout() 可将全部路径指向沙盒目录, 从而在非 macOS 环境用
      仿真的 macOS 目录树做完整的单元/接口测试(不触碰真实数据);
    - 删除前对每个目标做双重校验(validate_target): 必须位于允许的残留根之下
      (符号链接解析后同样必须在范围内) 且末级名称必须与 Bundle ID/应用名
      边界匹配, 防止本模块的删除接口被利用为任意文件删除;
    - 校验采用"全有或全无": 只要有一个目标不合法, 整次卸载直接拒绝, 一个文件都不删。
"""
from __future__ import annotations

import glob
import logging
import os
import plistlib
import platform
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

IS_MACOS = platform.system() == "Darwin"

# 环境变量钩子: 指向一个仿真 macOS 目录树的沙盒根目录(仅用于测试/演示)
SANDBOX_ENV = "DEEPCLEAN_APP_UNINSTALL_SANDBOX"

# ---------- 残留分类 ----------
CAT_APP = "应用本体"
CAT_PREF = "偏好设置"
CAT_CACHE = "缓存"
CAT_SUPPORT = "应用数据"
CAT_CONTAINER = "沙盒容器"
CAT_GROUP = "群组容器"
CAT_NET = "网络缓存"
CAT_WEBKIT = "网页视图缓存"
CAT_STATE = "窗口状态"
CAT_COOKIE = "Cookie"
CAT_LOG = "日志"
CAT_REPORT = "崩溃报告"
CAT_LAUNCH = "启动项"
CAT_PRIV_HELPER = "特权助手"
CAT_RECEIPT = "安装收据"
CAT_DOTFILE = "用户隐藏配置"
CAT_DOWNLOAD = "应用下载数据"
CAT_INSTALLER = "安装包"
CAT_SYSCACHE = "系统临时缓存"

# 这两类属于用户数据/安装包: 即使命中也默认不勾选, 由用户手动确认
NOAUTO_CATEGORIES = {CAT_DOWNLOAD, CAT_INSTALLER}

# 用户级残留根(相对 ~/Library) —— (相对路径, 分类)
USER_RESIDUE_DIRS = [
    ("Preferences", CAT_PREF),
    ("Preferences/ByHost", CAT_PREF),
    ("Caches", CAT_CACHE),
    ("Application Support", CAT_SUPPORT),
    ("Containers", CAT_CONTAINER),
    ("Group Containers", CAT_GROUP),
    ("HTTPStorages", CAT_NET),
    ("WebKit", CAT_WEBKIT),
    ("Saved Application State", CAT_STATE),
    ("Cookies", CAT_COOKIE),
    ("Logs", CAT_LOG),
    ("Logs/DiagnosticReports", CAT_REPORT),
    ("LaunchAgents", CAT_LAUNCH),
]

# 系统级残留根(相对 /Library) —— 需要管理员权限
SYSTEM_RESIDUE_DIRS = [
    ("Preferences", CAT_PREF),
    ("Caches", CAT_CACHE),
    ("Application Support", CAT_SUPPORT),
    ("LaunchAgents", CAT_LAUNCH),
    ("LaunchDaemons", CAT_LAUNCH),
    ("Logs", CAT_LOG),
    ("PrivilegedHelperTools", CAT_PRIV_HELPER),
]

# 名称边界字符: Bundle ID / 应用名匹配时, 命中片段前后必须是这些字符或字符串边界
_BOUNDARY_CHARS = " ._-(/"

VALID_MODES = ("trash", "permanent")


class InvalidAppPathError(ValueError):
    """应用路径不在允许的应用目录内"""


class UnsafePathError(ValueError):
    """存在不合法的删除目标(整体拒绝, 一个都不删)"""

    def __init__(self, rejected):
        self.rejected = rejected
        detail = "; ".join(f"{r['path']}({r['reason']})" for r in rejected)
        super().__init__(f"存在不合法的删除目标, 已整体拒绝执行: {detail}")


@dataclass
class MacLayout:
    """macOS 目录布局 —— 全部路径均可注入, 便于沙盒测试"""

    home: str
    applications: list = field(default_factory=list)          # 应用搜索目录
    user_residue_roots: list = field(default_factory=list)    # [(path, category, scope)]
    system_residue_roots: list = field(default_factory=list)  # [(path, category, scope)]
    receipts_dir: str = ""                                    # pkgutil 收据目录
    trash: str = ""                                           # 废纸篓
    protected_prefixes: list = field(default_factory=list)     # 系统应用前缀(受保护)
    var_folders: str = ""                                     # /private/var/folders(应用系统临时缓存)
    extra_roots: list = field(default_factory=list)           # 额外残留根 [(path, category, scope)]
    is_macos: bool = IS_MACOS

    def residue_roots(self):
        roots = (list(self.user_residue_roots) + list(self.system_residue_roots)
                 + list(self.extra_roots))
        if self.receipts_dir:
            roots.append((self.receipts_dir, CAT_RECEIPT, "system"))
        # 应用的系统级临时缓存: /private/var/folders/<x>/<y>/C|T(用户所有)
        if self.var_folders and os.path.isdir(self.var_folders):
            for tail in ("C", "T"):
                for sub in glob.glob(os.path.join(self.var_folders, "*", "*", tail)):
                    roots.append((sub, CAT_SYSCACHE, "user"))
        return roots


def default_layout() -> MacLayout:
    home = os.path.expanduser("~")
    lib = os.path.join(home, "Library")
    return MacLayout(
        home=home,
        applications=[
            "/Applications",
            os.path.join(home, "Applications"),
            "/System/Applications",
        ],
        user_residue_roots=[
            (os.path.join(lib, rel), cat, "user") for rel, cat in USER_RESIDUE_DIRS
        ],
        system_residue_roots=[
            (os.path.join("/Library", rel), cat, "system") for rel, cat in SYSTEM_RESIDUE_DIRS
        ],
        receipts_dir="/private/var/db/receipts",
        trash=os.path.join(home, ".Trash"),
        protected_prefixes=["/System", "/Library/Apple"],
        var_folders="/private/var/folders",
        extra_roots=[
            (os.path.join(home, "Downloads"), CAT_DOWNLOAD, "user"),
            (os.path.join(os.path.dirname(os.path.normpath(home)), "Shared", "PKGgs"),
             CAT_INSTALLER, "user"),
        ],
        is_macos=IS_MACOS,
    )


def sandbox_layout(root: str) -> MacLayout:
    """基于沙盒根目录构造仿真 macOS 布局(测试用: 永不触发 Finder/pkgutil/提权)"""
    root = os.path.abspath(root)
    home = os.path.join(root, "Users", "tester")
    return MacLayout(
        home=home,
        applications=[
            os.path.join(root, "Applications"),
            os.path.join(home, "Applications"),
            os.path.join(root, "System", "Applications"),
        ],
        user_residue_roots=[
            (os.path.join(home, "Library", rel), cat, "user") for rel, cat in USER_RESIDUE_DIRS
        ],
        system_residue_roots=[
            (os.path.join(root, "Library", rel), cat, "system") for rel, cat in SYSTEM_RESIDUE_DIRS
        ],
        receipts_dir=os.path.join(root, "private", "var", "db", "receipts"),
        trash=os.path.join(home, ".Trash"),
        protected_prefixes=[os.path.join(root, "System")],
        var_folders=os.path.join(root, "private", "var", "folders"),
        extra_roots=[
            (os.path.join(home, "Downloads"), CAT_DOWNLOAD, "user"),
            (os.path.join(os.path.dirname(os.path.normpath(home)), "Shared", "PKGgs"),
             CAT_INSTALLER, "user"),
        ],
        is_macos=False,
    )


_LAYOUT: MacLayout | None = None


def get_layout() -> MacLayout:
    global _LAYOUT
    if _LAYOUT is None:
        sandbox = os.environ.get(SANDBOX_ENV, "").strip()
        _LAYOUT = sandbox_layout(sandbox) if sandbox else default_layout()
    return _LAYOUT


def set_layout(layout: MacLayout | None) -> None:
    """注入布局(测试用); 传 None 恢复默认"""
    global _LAYOUT
    _LAYOUT = layout


# ==================== 基础工具 ====================

def _is_under(path: str, root: str) -> bool:
    """path 是否等于 root 或位于 root 之下(基于字符串前缀, POSIX)"""
    if not path or not root:
        return False
    path = os.path.normpath(path)
    root = os.path.normpath(root)
    if path == root:
        return True
    return path.startswith(root.rstrip("/") + "/")


def _boundary_match(name: str, needle: str) -> bool:
    """name 中是否存在与 needle 的边界对齐的命中(前/后须为分隔符或字符串边界)

    例: "com.vendor.foo.plist" 命中 "com.vendor.foo"; "com.vendor.foobar" 不命中。
    """
    if not name or not needle:
        return False
    n, nd = name.lower(), needle.lower()
    start = 0
    while True:
        i = n.find(nd, start)
        if i < 0:
            return False
        before_ok = i == 0 or n[i - 1] in _BOUNDARY_CHARS
        j = i + len(nd)
        after_ok = j == len(n) or n[j] in _BOUNDARY_CHARS
        if before_ok and after_ok:
            return True
        start = i + 1


def _app_name_match(entry: str, app_name: str) -> bool:
    """末级名称是否与应用名匹配: 完全相等, 或以应用名 + 分隔符开头"""
    if not entry or not app_name:
        return False
    a = app_name.strip().lower()
    if a.endswith(".app"):
        a = a[:-4]
    if not a:
        return False
    e = entry.lower()
    if e == a:
        return True
    if len(a) < 3:
        # 名称过短时不做前缀匹配, 避免误伤(如 "X2" 命中 "X2b"? )
        return False
    if e.startswith(a) and len(e) > len(a) and e[len(a)] in " -_(.":
        return True
    return False


def _fuzzy_contains(name: str, needles) -> bool:
    """模糊(纯包含)匹配 —— 只用于生成"疑似"候选(默认不勾选), 不要求边界"""
    if not name:
        return False
    n = name.lower()
    return any(nd in n for nd in needles)


def _fuzzy_needles(bundle_id: str, names) -> set:
    """构造模糊匹配关键字: 已知名称(>=4字) + Bundle ID 分段(>=7字)

    - 不用完整 Bundle ID: "com.vendor.foo" 是 "com.vendor.foo2" 的子串,
      纯包含会放过另一个应用的文件(边界匹配才能区分子串关系);
    - 分段门槛 7 是为了排除 "vendor"/"google"/"mac"/"tool" 这类通用词的噪声。
    """
    needles = set()
    for nm in names or ():
        nm = (nm or "").strip().lower()
        if len(nm) >= 4:
            needles.add(nm)
    if bundle_id:
        for seg in re.split(r"[.\-_\s]+", bundle_id.lower()):
            if len(seg) >= 7:
                needles.add(seg)
    return needles


def _parse_strings_file(path: str) -> dict:
    """解析 InfoPlist.strings: 兼容 plist 格式与旧式 key = "value"; 文本格式"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {}
    try:
        data = plistlib.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    text = raw.decode("utf-8", "ignore")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return dict(re.findall(r'([A-Za-z0-9_.]+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', text))


def _read_localized_names(app_path: str) -> dict:
    """读取 Contents/Resources/<lang>.lproj/InfoPlist.strings → {locale: {...}}"""
    res_dir = os.path.join(app_path, "Contents", "Resources")
    out = {}
    try:
        entries = os.listdir(res_dir)
    except OSError:
        return out
    for entry in entries:
        if not entry.lower().endswith(".lproj"):
            continue
        p = os.path.join(res_dir, entry, "InfoPlist.strings")
        if not os.path.isfile(p):
            continue
        data = _parse_strings_file(p)
        if data:
            out[entry[: -len(".lproj")]] = data
    return out


def _env_locale() -> str:
    """当前系统语言(zh_CN / en_US...), 未设置或 C/POSIX 时返回空串"""
    raw = (os.environ.get("LANGUAGE") or os.environ.get("LC_ALL")
           or os.environ.get("LANG") or "")
    base = raw.split(".")[0].strip().replace("-", "_")
    if not base or base.upper() in ("C", "POSIX"):
        return ""
    return base.lower()


def _locale_rank(locale: str) -> int:
    """locale 优先级: 与系统语言一致(0) > 中文变体(1) > 其它(2)"""
    loc = (locale or "").replace("-", "_").lower()
    lang = loc.split("_")[0]
    env = _env_locale()
    if env and (loc == env or lang == env.split("_")[0]):
        return 0
    if lang == "zh":
        return 1
    return 2


def _app_identity(app_path: str) -> tuple:
    """返回 (显示名, 全部已知名称集合)

    显示名优先级: 系统语言的本地化名(InfoPlist.strings, Finder 显示名) →
    Info.plist → 其它本地化名(中文优先) → 目录名;
    名称集合用于搜索与残留匹配 —— 例如目录名 "i4Tools" 而中文本地化名
    "爱思助手" 时, 两者都能被搜到。
    """
    stem = os.path.basename(os.path.normpath(app_path))
    if stem.lower().endswith(".app"):
        stem = stem[:-4]
    info = _read_info_plist(app_path)
    localized = _read_localized_names(app_path)
    names = {stem} if stem else set()
    for v in (info.get("display_name"), info.get("bundle_name")):
        if v:
            names.add(v)
    for locdata in localized.values():
        for k in ("CFBundleDisplayName", "CFBundleName"):
            v = (locdata.get(k) or "").strip()
            if v:
                names.add(v)
    def _localized_pick(ranks) -> str:
        for loc in sorted(localized, key=lambda l: (_locale_rank(l), l)):
            if _locale_rank(loc) not in ranks:
                continue
            for k in ("CFBundleDisplayName", "CFBundleName"):
                v = (localized[loc].get(k) or "").strip()
                if v:
                    return v
        return ""

    # 显示名优先级: 系统语言的本地化名(Finder 显示名) → Info.plist
    #             → 其它本地化名(中文优先) → 目录名
    display = _localized_pick({0})
    if not display:
        display = (info.get("display_name") or info.get("bundle_name")
                   or info.get("plist_name"))
    if not display:
        display = _localized_pick({1, 2})
    if not display:
        display = stem
    return display, names


def _collect_app_names(app_path: str) -> set:
    """应用全部已知名称(出错时退回目录名)"""
    try:
        return _app_identity(app_path)[1]
    except Exception:
        stem = os.path.basename(os.path.normpath(app_path))
        return {stem[:-4] if stem.lower().endswith(".app") else stem}


def _size_of(path: str) -> int:
    """文件/目录(含符号链接本体)占用字节数; 出错返回 0"""
    try:
        if os.path.islink(path):
            return os.lstat(path).st_size
        if os.path.isfile(path):
            return os.path.getsize(path)
        total = 0
        for _dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
            for fn in filenames:
                fp = os.path.join(_dirpath, fn)
                try:
                    total += os.lstat(fp).st_size
                except OSError:
                    continue
        return total
    except OSError:
        return 0


def _read_info_plist(app_path: str) -> dict:
    """读取 .app/Contents/Info.plist (XML 与二进制 plist 均可)"""
    info = os.path.join(app_path, "Contents", "Info.plist")
    try:
        with open(info, "rb") as f:
            data = plistlib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:  # plist 损坏/无权限等
        logger.debug("读取 Info.plist 失败 [%s]: %s", info, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "bundle_id": str(data.get("CFBundleIdentifier") or ""),
        "version": str(data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or ""),
        "display_name": str(data.get("CFBundleDisplayName") or ""),
        "bundle_name": str(data.get("CFBundleName") or ""),
        "plist_name": str(data.get("CFBundleDisplayName") or data.get("CFBundleName") or ""),
        "executable": str(data.get("CFBundleExecutable") or ""),
    }


def _iter_app_bundles(layout: MacLayout, max_depth: int = 4):
    """遍历应用搜索目录, 产出 .app 包路径(不进入 .app 内部, 限制深度)"""
    for base in layout.applications:
        if not os.path.isdir(base):
            continue
        stack = [(base, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                entries = sorted(os.listdir(current))
            except OSError:
                continue
            for entry in entries:
                if entry.startswith("."):
                    continue
                p = os.path.join(current, entry)
                if entry.lower().endswith(".app"):
                    yield p
                elif depth + 1 <= max_depth and os.path.isdir(p):
                    stack.append((p, depth + 1))


def _is_protected(layout: MacLayout, path: str) -> bool:
    """是否位于受保护的系统前缀之下"""
    return any(_is_under(path, p) for p in layout.protected_prefixes)


def _snapshot_processes() -> list:
    """进程快照(pid/name/exe/cmdline), 失败返回空列表"""
    snaps = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                snaps.append(proc.info)
            except Exception:
                continue
    except Exception as e:
        logger.debug("进程快照失败: %s", e)
    return snaps


def _matching_pids(snaps: list, app_path: str) -> list:
    """找出与应用包相关的进程: exe 位于包内, 或命令行中出现该包路径"""
    if not app_path:
        return []
    hits = []
    prefix = app_path.rstrip("/") + "/"
    for info in snaps:
        exe = info.get("exe") or ""
        cmdline = info.get("cmdline") or []
        try:
            in_exe = bool(exe) and (exe == app_path or exe.startswith(prefix))
            in_cmd = any(app_path in str(c) for c in cmdline)
        except Exception:
            continue
        if in_exe or in_cmd:
            hits.append({
                "pid": info.get("pid"),
                "name": info.get("name") or "",
            })
    return hits


# ==================== 1. 应用列表 ====================

def list_apps(with_sizes: bool = True, q: str = "", layout: MacLayout | None = None,
              sort_by: str = "size", order: str = "desc") -> list:
    """列出已安装应用; q 按显示名/别名(含本地化名)/Bundle ID 过滤(后端过滤)

    sort_by: size(默认,按体积) | name; order: desc(默认,降序) | asc
    """
    layout = layout or get_layout()
    if sort_by not in ("size", "name"):
        sort_by = "size"
    if order not in ("asc", "desc"):
        order = "desc"
    snaps = _snapshot_processes()
    apps = []
    seen = set()
    for path in _iter_app_bundles(layout):
        key = os.path.normpath(path)
        if key in seen:
            continue
        seen.add(key)
        info = _read_info_plist(key)
        display, aliases = _app_identity(key)
        apps.append({
            "name": display,
            "aliases": sorted(aliases),
            "path": key,
            "bundle_id": info.get("bundle_id", ""),
            "version": info.get("version", ""),
            "executable": info.get("executable", ""),
            "system": _is_protected(layout, key),
            "running": bool(_matching_pids(snaps, key)),
            "size": _size_of(key) if with_sizes else None,
        })
    # 先按名称稳定排序再按目标键排(体积默认降序, 并列时保持名称升序)
    apps.sort(key=lambda a: (a["name"] or "").lower())
    if sort_by == "size":
        apps.sort(key=lambda a: a["size"] or 0, reverse=(order != "asc"))
    else:
        apps.sort(key=lambda a: (a["name"] or "").lower(), reverse=(order == "desc"))
    if q:
        needle = q.strip().lower()
        apps = [a for a in apps
                if needle in (a["name"] or "").lower()
                or needle in (a["bundle_id"] or "").lower()
                or any(needle in (al or "").lower() for al in a["aliases"])]
    return apps


# ==================== 2. 残留分析 ====================

def find_residue(layout: MacLayout, bundle_id: str, app_name: str, app_path: str = "") -> list:
    """在各残留根下定位与该应用关联的文件/目录(不含应用本体)

    匹配分两层:
    - 强关联(bundle_id/name 边界匹配) → selected=True, 默认勾选;
    - 疑似(关键字纯包含, 如 "i4ToolsDownloads" 命中 i4Tools) → selected=False,
      仅列出供用户确认, 默认不勾选。
    """
    items: list = []
    seen: set = set()
    app_norm = os.path.normpath(os.path.abspath(app_path)) if app_path else ""
    trash_norm = os.path.normpath(os.path.abspath(layout.trash)) if layout.trash else ""

    names: set = {app_name} if app_name else set()
    if app_norm and os.path.isdir(app_norm):
        names |= _collect_app_names(app_norm)
    names = {n for n in names if n}
    fuzzy = _fuzzy_needles(bundle_id, names)

    def _tier(name: str, category: str):
        if bundle_id and _boundary_match(name, bundle_id):
            return "bundle_id"
        if category == CAT_RECEIPT:
            return None  # 安装收据只按 Bundle ID 匹配
        for nm in names:
            if _app_name_match(name, nm):
                return "name"
        if fuzzy and _fuzzy_contains(name, fuzzy):
            return "fuzzy"
        return None

    def _add(path: str, category: str, scope: str, match: str) -> None:
        try:
            key = os.path.normpath(os.path.abspath(path))
        except OSError:
            return
        if key in seen:
            return
        if app_norm and key == app_norm:
            return
        if trash_norm and key == trash_norm:
            return  # 绝不能把废纸篓本身当成残留删掉
        seen.add(key)
        items.append({
            "path": key,
            "name": os.path.basename(key),
            "category": category,
            "scope": scope,
            "needs_admin": scope == "system",
            "type": "dir" if (os.path.isdir(path) and not os.path.islink(path)) else "file",
            "size": _size_of(path),
            "match": match,
            "selected": match != "fuzzy" and category not in NOAUTO_CATEGORIES,
        })

    def _scan(root: str, category: str, scope: str) -> None:
        """扫一层; 应用数据目录额外扫第二层(如 Application Support/CrashReporter/)"""
        if not os.path.isdir(root):
            return
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            return
        root_base = os.path.basename(os.path.normpath(root))
        unmatched_dirs = []
        for name in entries:
            if name.startswith("."):
                continue
            if name == root_base:
                continue  # 不允许命中根目录自身
            match = _tier(name, category)
            if match:
                _add(os.path.join(root, name), category, scope, match)
            elif category == CAT_SUPPORT and os.path.isdir(os.path.join(root, name)):
                unmatched_dirs.append(name)
        if category == CAT_SUPPORT:
            # 第一层已命中的目录整体删除即可, 不再把其子文件列成独立项
            for sub in unmatched_dirs:
                sub_path = os.path.join(root, sub)
                try:
                    sub_entries = sorted(os.listdir(sub_path))
                except OSError:
                    continue
                for name in sub_entries:
                    if name.startswith("."):
                        continue
                    match = _tier(name, category)
                    if match:
                        _add(os.path.join(sub_path, name), category, scope, match)

    for root, category, scope in layout.residue_roots():
        _scan(root, category, scope)

    # ~/.<AppName> 形式的用户隐藏配置(仅精确/边界匹配, 不做模糊)
    try:
        for name in sorted(os.listdir(layout.home)):
            if not name.startswith("."):
                continue
            inner = name[1:]
            if any(_app_name_match(inner, nm) for nm in names):
                _add(os.path.join(layout.home, name), CAT_DOTFILE, "user", "name")
    except OSError:
        pass

    items.sort(key=lambda x: x["size"], reverse=True)
    return items


def analyze_app(app_path: str, layout: MacLayout | None = None) -> dict:
    """分析单个应用: 应用本体 + 全部关联残留"""
    layout = layout or get_layout()
    norm = os.path.normpath(os.path.abspath(os.path.expanduser(app_path or "")))
    if not _valid_app_path(layout, norm):
        raise InvalidAppPathError(f"应用路径不在允许的应用目录内: {app_path}")
    if not os.path.isdir(norm):
        raise FileNotFoundError(app_path)

    info = _read_info_plist(norm)
    bundle_id = info.get("bundle_id", "")
    app_name, aliases = _app_identity(norm)

    snaps = _snapshot_processes()
    pids = _matching_pids(snaps, norm)
    items = find_residue(layout, bundle_id, app_name, norm)

    system = _is_protected(layout, norm)
    items.insert(0, {
        "path": norm,
        "name": os.path.basename(norm),
        "category": CAT_APP,
        "scope": "system" if system else "user",
        "needs_admin": system,
        "type": "dir",
        "size": _size_of(norm),
        "match": "app",
        "selected": True,
    })
    items.sort(key=lambda x: x["size"], reverse=True)

    warnings = []
    if not bundle_id:
        warnings.append("未能读取 Bundle ID, 残留文件仅按应用名匹配, 可能不完整")
    fuzzy_items = [i for i in items if i.get("match") == "fuzzy"]
    if fuzzy_items:
        warnings.append(f"另有 {len(fuzzy_items)} 项按关键字模糊匹配的疑似残留, "
                        f"已默认取消勾选, 请确认后再删除")
    if pids:
        warnings.append(f"应用正在运行({len(pids)} 个相关进程), 卸载前将尝试退出")
    if system:
        warnings.append("这是系统应用, 部分文件受 SIP 保护, 删除可能被系统拒绝")

    return {
        "app": {
            "name": app_name,
            "aliases": sorted(aliases),
            "path": norm,
            "bundle_id": bundle_id,
            "version": info.get("version", ""),
            "system": system,
            "running": bool(pids),
            "size": _size_of(norm),
        },
        "items": items,
        "total_size": sum(i["size"] for i in items),
        "running": bool(pids),
        "pids": pids,
        "warnings": warnings,
    }


# ==================== 3. 目标校验 ====================

def _valid_app_path(layout: MacLayout, path: str) -> bool:
    """应用本体路径是否合法: 位于应用搜索目录之下且以 .app 结尾"""
    if not path or not path.lower().endswith(".app"):
        return False
    return any(_is_under(path, base) for base in layout.applications)


def validate_target(layout: MacLayout, path: str, *, app_path: str = "",
                    bundle_id: str = "", app_name: str = "",
                    aliases=None) -> tuple:
    """删除前校验单个目标: 返回 (是否合法, 说明)"""
    if not path or not str(path).strip():
        return False, "路径为空"
    norm = os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))
    if norm == "/":
        return False, "不能删除根目录"
    if norm == os.path.normpath(layout.home):
        return False, "不能删除用户主目录"

    # 应用本体
    if app_path and norm == os.path.normpath(os.path.abspath(app_path)):
        if _valid_app_path(layout, norm):
            return True, "应用本体"
        return False, "应用路径不在允许的应用目录内"

    name = os.path.basename(norm)

    # 已知名称(含本地化别名)与模糊关键字 —— 服务端从应用包自行派生, 不信任客户端
    names = set()
    if app_name:
        names.add(app_name)
    if aliases:
        names.update(a for a in aliases if a)
    if app_path and os.path.isdir(app_path):
        names |= _collect_app_names(app_path)
    fuzzy = _fuzzy_needles(bundle_id, names)

    # 必须位于允许的残留根之下, 且符号链接解析后仍在该根之内
    for root, category, _scope in layout.residue_roots():
        if not _is_under(norm, root):
            continue
        real = os.path.realpath(norm)
        if not _is_under(real, os.path.realpath(root)):
            return False, "经由符号链接指向允许范围之外"
        if category == CAT_RECEIPT:
            if bundle_id and _boundary_match(name, bundle_id):
                return True, "Bundle ID 匹配(安装收据)"
            return False, "安装收据目录仅允许按 Bundle ID 匹配"
        if bundle_id and _boundary_match(name, bundle_id):
            return True, "Bundle ID 匹配"
        if any(_app_name_match(name, nm) for nm in names):
            return True, "应用名匹配"
        if fuzzy and _fuzzy_contains(name, fuzzy):
            return True, "关键字模糊匹配(用户手动勾选)"
        return False, "名称与该应用不匹配"

    # ~/.<AppName> 用户隐藏配置
    if os.path.dirname(norm) == os.path.normpath(layout.home) and name.startswith("."):
        real = os.path.realpath(norm)
        if not _is_under(real, os.path.realpath(layout.home)):
            return False, "经由符号链接指向允许范围之外"
        if any(_app_name_match(name[1:], nm) for nm in names):
            return True, "用户隐藏配置匹配"
        return False, "名称与该应用不匹配"

    return False, "不在允许的残留目录范围内"


# ==================== 4. 执行卸载 ====================

def _trash_dest(layout: MacLayout, path: str) -> str:
    """计算废纸篓中的目标路径(重名则加序号)"""
    os.makedirs(layout.trash, exist_ok=True)
    base = os.path.basename(os.path.normpath(path))
    dest = os.path.join(layout.trash, base)
    if not os.path.lexists(dest):
        return dest
    stem, ext = os.path.splitext(base)
    i = 1
    while os.path.lexists(dest):
        dest = os.path.join(layout.trash, f"{stem} {i}{ext}")
        i += 1
    return dest


def _as_applescript_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _trash(layout: MacLayout, path: str) -> tuple:
    """移入废纸篓: macOS 优先走 Finder(可撤销/可恢复), 失败或其它环境手动移动"""
    if layout.is_macos and shutil.which("osascript"):
        script = f'tell application "Finder" to delete POSIX file "{_as_applescript_string(path)}"'
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                return True, "已通过 Finder 移入废纸篓"
            logger.debug("Finder 移入废纸篓失败, 回退手动移动: %s", (r.stderr or "").strip())
        except Exception as e:
            logger.debug("Finder 移入废纸篓异常, 回退手动移动: %s", e)
    dest = _trash_dest(layout, path)
    shutil.move(path, dest)  # 同卷为 rename; 跨卷自动 copy + remove
    return True, "已移入废纸篓"


def _purge(path: str) -> None:
    """永久删除(符号链接只删链接本身)"""
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    else:
        shutil.rmtree(path)


def _admin_op(layout: MacLayout, path: str, mode: str) -> str:
    """macOS 提权执行: trash 模式提权移动到废纸篓, permanent 模式提权 rm -rf"""
    if not (layout.is_macos and shutil.which("osascript")):
        raise PermissionError("需要管理员权限(当前环境无法提权)")
    if mode == "trash":
        dest = _trash_dest(layout, path)
        cmd = f"mv {_shell_quote(path)} {_shell_quote(dest)}"
    else:
        cmd = f"rm -rf {_shell_quote(path)}"
    script = f'do shell script "{_as_applescript_string(cmd)}" with administrator privileges'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        err = (r.stderr or "").strip() or "用户取消或提权失败"
        raise PermissionError(err)
    return "已提权处理"


def _shell_quote(s: str) -> str:
    return shlex.quote(s)


def _remove_one(layout: MacLayout, path: str, mode: str) -> dict:
    """删除单个目标并返回结果(不抛异常, 失败记入结果)"""
    if not os.path.lexists(path):
        return {"path": path, "status": "skipped", "size": 0, "message": "路径不存在(可能已被删除)"}
    size = _size_of(path)
    status, message = "failed", ""
    try:
        if mode == "trash":
            ok, message = _trash(layout, path)
            status = "trashed" if ok else "failed"
        else:
            _purge(path)
            status, message = "deleted", "已永久删除"
    except PermissionError as e:
        if layout.is_macos:
            try:
                message = _admin_op(layout, path, mode)
                status = "trashed" if mode == "trash" else "deleted"
            except PermissionError as e2:
                message = f"权限不足(提权失败): {e2}"
            except Exception as e2:
                message = f"提权处理失败: {e2}"
        else:
            message = f"权限不足: {e}"
    except OSError as e:
        message = f"删除失败: {e}"
    except Exception as e:  # 兜底: 单项失败不能中断整体流程
        logger.exception("删除异常: %s", path)
        message = f"删除异常: {e}"
    if status == "failed":
        logger.warning("删除失败 [%s]: %s", path, message)
    return {"path": path, "status": status, "size": size, "message": message}


def find_app_pids(app_path: str, layout: MacLayout | None = None) -> list:
    """与应用包相关的进程列表"""
    return _matching_pids(_snapshot_processes(), os.path.normpath(os.path.abspath(app_path)))


def stop_app_processes(app_path: str, layout: MacLayout | None = None) -> list:
    """优雅退出与应用相关的进程(先 terminate, 5 秒未退出再 kill)"""
    hits = find_app_pids(app_path, layout)
    if not hits:
        return []
    try:
        import psutil
    except Exception as e:
        logger.debug("psutil 不可用, 跳过结束进程: %s", e)
        return []

    results = []
    procs = []

    def _pname(p):
        try:
            return p.name()
        except Exception:
            return ""

    for h in hits:
        pid = h.get("pid")
        if not pid or pid <= 1 or pid == os.getpid():
            continue
        try:
            procs.append(psutil.Process(pid))
        except Exception:
            continue

    alive = []
    for p in procs:
        try:
            p.terminate()
            alive.append(p)
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            results.append({"pid": p.pid, "name": _pname(p), "action": "failed", "message": str(e)})

    if alive:
        _gone, still = psutil.wait_procs(alive, timeout=5)
        for p in still:
            try:
                p.kill()
                results.append({"pid": p.pid, "name": _pname(p), "action": "killed"})
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                results.append({"pid": p.pid, "name": "", "action": "failed", "message": str(e)})
        gone_pids = {g.pid for g in _gone}
        for p in alive:
            if p.pid in gone_pids:
                results.append({"pid": p.pid, "name": _pname(p), "action": "terminated"})
    return results


def _pkgutil_forget(bundle_id: str) -> bool:
    """pkgutil --forget 忘记安装收据"""
    if not shutil.which("pkgutil"):
        return False
    try:
        r = subprocess.run(["pkgutil", "--forget", bundle_id],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        logger.debug("pkgutil --forget 异常: %s", e)
        return False
    if r.returncode == 0:
        logger.info("已忘记安装收据: %s", bundle_id)
        return True
    logger.debug("pkgutil --forget 失败: %s", (r.stderr or "").strip())
    return False


def uninstall_app(app_path: str, paths: list, *, bundle_id: str = "", app_name: str = "",
                  mode: str = "trash", stop_processes: bool = True,
                  forget_receipts: bool = True, layout: MacLayout | None = None) -> dict:
    """深度卸载: 先整体校验(全有或全无), 再结束进程, 最后逐项删除"""
    layout = layout or get_layout()
    if mode not in VALID_MODES:
        raise ValueError(f"不支持的删除方式: {mode} (可选: {'/'.join(VALID_MODES)})")
    if not paths:
        raise ValueError("未选择任何要删除的路径")

    norm_app = os.path.normpath(os.path.abspath(os.path.expanduser(app_path or ""))) if app_path else ""
    if norm_app and not _valid_app_path(layout, norm_app):
        raise InvalidAppPathError(f"应用路径不在允许的应用目录内: {app_path}")

    # ① 预校验: 只要有一个目标不合法, 整体拒绝
    aliases = set()
    if app_name:
        aliases.add(app_name)
    if norm_app and os.path.isdir(norm_app):
        aliases |= _collect_app_names(norm_app)
    rejected = []
    for p in paths:
        ok, reason = validate_target(layout, p, app_path=norm_app,
                                     bundle_id=bundle_id, app_name=app_name,
                                     aliases=aliases)
        if not ok:
            rejected.append({"path": str(p), "reason": reason})
    if rejected:
        raise UnsafePathError(rejected)

    # ② 规范化 + 去重 + 由浅到深排序(父目录先删, 子项随后 skipped, 避免大小重复统计)
    targets, seen = [], set()
    for p in paths:
        k = os.path.normpath(os.path.abspath(os.path.expanduser(str(p))))
        if k not in seen:
            seen.add(k)
            targets.append(k)
    targets.sort(key=lambda p: (p.count(os.sep), len(p)))

    # ③ 结束运行中的进程
    stopped = []
    if stop_processes and norm_app:
        stopped = stop_app_processes(norm_app, layout)

    # ④ 逐项删除
    results = [_remove_one(layout, p, mode) for p in targets]

    # ⑤ 忘记安装收据(仅当选中的目标包含收据目录且在 macOS 上)
    receipt_targeted = bool(layout.receipts_dir) and any(
        _is_under(p, layout.receipts_dir) for p in targets)
    forgotten = False
    if forget_receipts and bundle_id and receipt_targeted and layout.is_macos:
        forgotten = _pkgutil_forget(bundle_id)

    ok_statuses = ("trashed", "deleted")
    failed = [r for r in results if r["status"] == "failed"]
    return {
        "status": "success" if not failed else "partial",
        "mode": mode,
        "results": results,
        "removed_count": sum(1 for r in results if r["status"] in ok_statuses),
        "failed_count": len(failed),
        "skipped_count": sum(1 for r in results if r["status"] == "skipped"),
        "total_size": sum(r["size"] for r in results if r["status"] in ok_statuses),
        "stopped": stopped,
        "receipt_forgotten": forgotten,
    }
