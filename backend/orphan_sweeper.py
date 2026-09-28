"""无主残留(孤儿残留)扫描与清理 —— 清理"已删除目标"遗留的残留

与应用深度卸载(针对"还在的应用")互补: 这里针对**已经不存在**的目标 —
应用被删掉/被本软件卸载后, 它的偏好设置、缓存、容器、收据等仍散落在
残留根里; 文件被删除后, 同名/同基的伴生残留也可能还在。

证据链(从强到弱, 决定默认是否勾选):
- bundle:       残留名是反向域名 Bundle ID 形态, 且**不属于任何已安装应用**,
                也非系统前缀(com.apple 等) → 无主残留, 默认勾选
                (需已安装应用清单非空, 否则判定不可靠 → 降级为不勾选);
- history:      与删除历史中"被本软件卸载过的应用"的名称(边界匹配)命中
                → 默认勾选(当时的漏网之鱼, 现在可追踪);
- file_history: 与历史中已删除文件的基名(边界匹配)命中 → 疑似, 默认不勾选。

安全约束与应用卸载一致: 目标必须 realpath 落在允许残留根之内 + 名称证据匹配,
任一目标不合法则整体拒绝; 收据目录只认 Bundle ID 形态; 系统前缀永不放行。
"""
import logging
import os
import re

import app_uninstaller as au
import history as hist

logger = logging.getLogger("DeepClean.orphan")

# 反向域名形态: 至少 3 段(≥2 个点), 段内允许连字符/下划线
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*){2,}$")

# 系统/包管理前缀 —— 永不视为可清理的无主残留
_SYSTEM_PREFIXES = ("com.apple", "apple", "org.macosforge", "org.macports", "com.macports")

_EVIDENCE_DESC = {
    "bundle": "Bundle ID 未找到对应应用",
    "history": "本软件卸载过的应用",
    "file_history": "已删除文件的同类残留(疑似)",
}


class Evidence:
    """判定证据集合"""

    def __init__(self, installed_bundle_ids, history_bundle_ids, history_names, file_stems):
        self.installed = installed_bundle_ids
        self.hist_bids = history_bundle_ids
        self.hist_names = history_names
        self.file_stems = file_stems

    @property
    def has_app_inventory(self) -> bool:
        """已安装应用清单是否可信(清单为空时 Bundle ID 判定不可靠)"""
        return bool(self.installed)


def build_evidence(layout: "au.MacLayout | None" = None) -> Evidence:
    layout = layout or au.get_layout()
    installed = set()
    try:
        for app in au.list_apps(with_sizes=False, layout=layout):
            bid = (app.get("bundle_id") or "").strip().lower()
            if bid:
                installed.add(bid)
    except Exception as e:  # 清单拿不到时退化为"无证据", 由 has_app_inventory 兜底
        logger.warning("获取已安装应用清单失败: %s", e)
    hist_bids, hist_names = hist.app_evidence()
    stems = hist.file_evidence()
    return Evidence(installed, hist_bids, hist_names, stems)


def _is_system(name: str) -> bool:
    n = (name or "").lower()
    return any(n == p or n.startswith(p + ".") for p in _SYSTEM_PREFIXES)


def _owned_by_installed(candidate: str, ev: Evidence) -> bool:
    """候选 Bundle ID 是否被某个已安装应用拥有(相等或父子前缀关系)"""
    n = (candidate or "").lower()
    for b in ev.installed:
        if n == b or n.startswith(b + ".") or b.startswith(n + "."):
            return True
    return False


def _bundle_related_to_history(candidate: str, ev: Evidence) -> bool:
    n = (candidate or "").lower()
    for b in ev.hist_bids:
        if n == b or n.startswith(b + ".") or b.startswith(n + "."):
            return True
    return False


def classify(name: str, category: str, ev: Evidence):
    """判定单个残留条目的证据层级 → (match | None, 说明)

    返回 None = 不算残留(归属已安装应用 / 证据不足 / 系统组件)。
    系统组件返回 ("__system__", ...) 供统计排除数。
    """
    if not name:
        return None, ""
    base = name
    is_receipt = category == au.CAT_RECEIPT
    if is_receipt:
        for ext in (".plist", ".bom"):
            if base.lower().endswith(ext):
                base = base[: -len(ext)]
                break

    # ① Bundle ID 形态(收据目录只认这一种)
    if BUNDLE_ID_RE.match(base):
        if _is_system(base):
            return "__system__", "系统组件前缀"
        if _owned_by_installed(base, ev):
            return None, ""
        if _bundle_related_to_history(base, ev):
            return "history", _EVIDENCE_DESC["history"]
        return "bundle", _EVIDENCE_DESC["bundle"]
    if is_receipt:
        return None, ""  # 收据里非 Bundle ID 形态 → 不动

    # ② 历史卸载应用的名称(边界匹配, 防 Foo 误伤 Foobar)
    for nm in ev.hist_names:
        if au._app_name_match(name, nm):
            return "history", _EVIDENCE_DESC["history"]

    # ③ 历史已删除文件的基名(边界匹配) → 疑似层
    lname = name.lower()
    for stem in ev.file_stems:
        if au._app_name_match(lname, stem):
            return "file_history", _EVIDENCE_DESC["file_history"]
    return None, ""


def _make_item(path: str, category: str, scope: str, match: str, ev: Evidence) -> dict:
    st = None
    try:
        st = os.lstat(path)
    except OSError:
        pass
    is_dir = st is not None and (st.st_mode & 0o170000) == 0o040000
    selected = (
        match == "history"
        or (match == "bundle" and ev.has_app_inventory)
    )
    return {
        "path": os.path.normpath(os.path.abspath(path)),
        "name": os.path.basename(path),
        "category": category,
        "scope": scope,
        "needs_admin": scope == "system",
        "type": "dir" if is_dir else "file",
        "size": au._size_of(path) if st else 0,
        "match": match,
        "selected": selected,
        "evidence": _EVIDENCE_DESC.get(match, ""),
    }


def scan(layout: "au.MacLayout | None" = None) -> dict:
    """扫描所有残留根下的无主/历史残留"""
    layout = layout or au.get_layout()
    ev = build_evidence(layout)

    items: list = []
    seen: set = set()
    excluded_system = 0

    def add(path, category, scope, match):
        try:
            key = os.path.normpath(os.path.abspath(path))
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        items.append(_make_item(key, category, scope, match, ev))

    def handle(name, root, category, scope):
        nonlocal excluded_system
        if name.startswith("."):
            return None
        if name == os.path.basename(os.path.normpath(root)):
            return None  # 不允许命中根目录自身
        m, _reason = classify(name, category, ev)
        if m == "__system__":
            excluded_system += 1
            return None
        if m:
            add(os.path.join(root, name), category, scope, m)
            return m
        return None

    def scan_root(root, category, scope):
        if not os.path.isdir(root):
            return
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            return
        unmatched_dirs = []
        for name in entries:
            m = handle(name, root, category, scope)
            if m is None and not name.startswith("."):
                if category == au.CAT_SUPPORT and os.path.isdir(os.path.join(root, name)):
                    unmatched_dirs.append(name)
        if category == au.CAT_SUPPORT:
            for sub in unmatched_dirs:  # 二级目录(如 CrashReporter/), 一级已命中的不再拆
                sub_path = os.path.join(root, sub)
                try:
                    sub_entries = sorted(os.listdir(sub_path))
                except OSError:
                    continue
                for name in sub_entries:
                    handle(name, sub_path, category, scope)

    for root, category, scope in layout.residue_roots():
        scan_root(root, category, scope)

    # ~/.<AppName> 形式的用户隐藏配置(对历史应用名做边界匹配)
    if ev.hist_names:
        try:
            for name in sorted(os.listdir(layout.home)):
                if not name.startswith("."):
                    continue
                inner = name[1:]
                if any(au._app_name_match(inner, nm) for nm in ev.hist_names):
                    add(os.path.join(layout.home, name), au.CAT_DOTFILE, "user", "history")
        except OSError:
            pass

    # 强证据在前, 组内按体积降序(与深度卸载清单同口径)
    items.sort(key=lambda x: (0 if x["selected"] else 1, -x["size"]))

    warnings = []
    if not ev.has_app_inventory:
        warnings.append("未读取到已安装应用清单(当前平台可能不支持), "
                        "Bundle ID 无主判定已降级为默认不勾选, 请人工确认")
    if excluded_system:
        warnings.append(f"已排除 {excluded_system} 项系统组件(com.apple 等前缀)")
    if not ev.hist_bids and not ev.hist_names and not ev.file_stems:
        warnings.append("删除历史为空, 本次仅能识别『Bundle ID 无主』一类残留")

    selected_items = [i for i in items if i["selected"]]
    return {
        "items": items,
        "summary": {
            "count": len(items),
            "bytes": sum(i["size"] for i in items),
            "selected_count": len(selected_items),
            "selected_bytes": sum(i["size"] for i in selected_items),
            "excluded_system": excluded_system,
            "installed_apps": len(ev.installed),
            "history_bundle_ids": len(ev.hist_bids),
            "history_names": len(ev.hist_names),
        },
        "warnings": warnings,
    }


def validate_target(path: str, layout: "au.MacLayout | None" = None,
                    ev: Evidence | None = None) -> tuple:
    """校验单个清理目标 → (ok, reason, category, scope)"""
    layout = layout or au.get_layout()
    ev = ev or build_evidence(layout)
    try:
        norm = os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))
    except (OSError, ValueError):
        return False, "路径不合法", "", ""
    real = os.path.realpath(norm)

    for root, category, scope in layout.residue_roots():
        try:
            if not os.path.isdir(root):
                continue
        except OSError:
            continue
        rr = os.path.realpath(root).rstrip("/")
        if not (real == rr or real.startswith(rr + "/")):
            continue
        if real == rr:
            return False, "不允许删除残留根目录本身", category, scope
        name = os.path.basename(norm)
        m, reason = classify(name, category, ev)
        if m == "__system__":
            return False, "系统组件前缀, 永不清理", category, scope
        if m:
            return True, reason, category, scope
        return False, "名称与已知残留证据不匹配", category, scope

    # ~/.<AppName> 点配置(仅限 home 直接子级, 不放行任意深度)
    if ev.hist_names:
        try:
            hr = os.path.realpath(layout.home).rstrip("/")
            if os.path.dirname(real) == hr and real != hr:
                name = os.path.basename(norm)
                if name.startswith("."):
                    inner = name[1:]
                    if any(au._app_name_match(inner, nm) for nm in ev.hist_names):
                        return True, _EVIDENCE_DESC["history"], au.CAT_DOTFILE, "user"
        except OSError:
            pass
    return False, "不在允许的残留目录范围内", "", ""


def delete_orphans(paths: list, mode: str = "trash",
                   layout: "au.MacLayout | None" = None,
                   forget_receipts: bool = True) -> dict:
    """清理无主残留 —— 先整体校验(任一不合法则整体拒绝), 再逐项删除"""
    layout = layout or au.get_layout()
    if mode not in ("trash", "permanent"):
        raise ValueError("mode 必须是 trash 或 permanent")
    ev = build_evidence(layout)

    targets: list = []
    seen: set = set()
    for p in (paths or []):
        try:
            norm = os.path.normpath(os.path.abspath(os.path.expanduser(str(p))))
        except (OSError, ValueError):
            raise au.UnsafePathError([{"path": str(p), "reason": "路径不合法"}])
        if norm in seen:
            continue
        seen.add(norm)
        targets.append(norm)
    if not targets:
        raise ValueError("没有提供任何清理目标")

    rejected = []
    validated = []  # [(path, category)]
    for t in targets:
        ok, reason, category, _scope = validate_target(t, layout, ev)
        if ok:
            validated.append((t, category))
        else:
            rejected.append({"path": t, "reason": reason})
    if rejected:
        raise au.UnsafePathError(rejected)

    # 逐项删除(与应用卸载共用 _remove_one: 废纸篓/提权/失败兜底)
    results = []
    for t, category in validated:
        r = au._remove_one(layout, t, mode)
        r["category"] = category
        results.append(r)

    # 收据目标 → pkgutil --forget(macOS)
    receipt_targets = [t for t, _c in validated
                       if layout.receipts_dir and au._is_under(t, layout.receipts_dir)]
    forgotten_ids = []
    if forget_receipts and receipt_targets and layout.is_macos:
        for t in receipt_targets:
            name = os.path.basename(t)
            for ext in (".plist", ".bom"):
                if name.lower().endswith(ext):
                    name = name[: -len(ext)]
                    break
            if BUNDLE_ID_RE.match(name) and name.lower() not in forgotten_ids:
                if au._pkgutil_forget(name):
                    forgotten_ids.append(name.lower())

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
        "receipts_forgotten": forgotten_ids,
    }
