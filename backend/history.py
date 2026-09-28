"""删除操作历史记录与贡献统计

所有通过本软件的删除(文件扫描 / 应用卸载 / 残留清理)统一在 API 层落账,
用于: ① 追踪删过哪些内容; ② 统计贡献(操作次数/条目数/释放空间/卸载应用数/清理残留数)。

设计要点:
- JSON 原子持久化(临时文件 + os.replace), 线程安全(全局锁);
- 单条记录的 items 明细截断到 500 条(count/bytes 始终为完整值);
- 记录条数上限 1000, 超出丢弃最旧的;
- 路径: DEEPCLEAN_HISTORY_PATH 环境变量 > ~/.deepclean/history.json(便于测试注入)。
"""
import json
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger("DeepClean.history")

_MAX_RECORDS = 1000
_MAX_ITEMS = 500
_lock = threading.Lock()


def history_path() -> str:
    p = (os.environ.get("DEEPCLEAN_HISTORY_PATH") or "").strip()
    if not p:
        p = os.path.join(os.path.expanduser("~"), ".deepclean", "history.json")
    return p


def _empty() -> dict:
    return {"version": 1, "records": []}


def _load() -> dict:
    p = history_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValueError("结构不合法")
        return data
    except FileNotFoundError:
        return _empty()
    except Exception as e:  # 损坏的文件不阻塞删除流程, 但保留现场便于排查
        logger.warning("历史文件读取失败, 按空记录处理: %s (%s)", p, e)
        return _empty()


def _save(data: dict) -> None:
    p = history_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = f"{p}.tmp.{os.getpid()}.{uuid.uuid4().hex[:6]}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def record(source: str, mode: str, items: list, *, app: dict | None = None,
           ok: bool = True, detail: str = "") -> dict:
    """记录一次删除操作

    source: file_scan | app_uninstall | orphan_residue
    mode:   permanent | trash
    items:  [{"path": ..., "size": int, "category": str|None}, ...]
    app:    应用卸载时的应用信息 {name, bundle_id, aliases, path}
    """
    norm_items = []
    for it in (items or []):
        if not isinstance(it, dict) or not it.get("path"):
            continue
        try:
            size = int(it.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        norm_items.append({
            "path": str(it["path"]),
            "size": max(0, size),
            "category": it.get("category") or "",
        })

    rec = {
        "id": uuid.uuid4().hex[:16],
        "ts": int(time.time()),
        "source": source,
        "mode": mode,
        "count": len(norm_items),
        "bytes": sum(i["size"] for i in norm_items),
        "items": norm_items[:_MAX_ITEMS],
        "items_truncated": len(norm_items) > _MAX_ITEMS,
        "app": app or None,
        "ok": bool(ok),
        "detail": detail,
    }
    with _lock:
        data = _load()
        data["records"].append(rec)
        if len(data["records"]) > _MAX_RECORDS:
            data["records"] = data["records"][-_MAX_RECORDS:]
        try:
            _save(data)
        except OSError as e:
            # 落盘失败不能影响删除结果本身
            logger.error("历史记录写入失败: %s", e)
    return rec


def list_records(limit: int = 50, offset: int = 0, source: str = "") -> dict:
    """分页读取记录(最新在前); source 可按来源过滤"""
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    with _lock:
        records = _load()["records"]
    if source:
        records = [r for r in records if r.get("source") == source]
    records = list(reversed(records))  # 最新在前
    total = len(records)
    return {"records": records[offset:offset + limit], "total": total}


def stats() -> dict:
    """聚合统计 —— 贡献看板的数据源"""
    with _lock:
        records = _load()["records"]

    ok_records = [r for r in records if r.get("ok")]
    by_source: dict = {}

    def blank():
        return {"operations": 0, "items": 0, "bytes": 0}

    for r in ok_records:
        src = r.get("source") or "unknown"
        bucket = by_source.setdefault(src, blank())
        bucket["operations"] += 1
        bucket["items"] += int(r.get("count") or 0)
        bucket["bytes"] += int(r.get("bytes") or 0)

    # 卸载应用数 = 应用卸载来源的成功操作次数(每次卸载一个应用)
    apps_uninstalled = by_source.get("app_uninstall", {}).get("operations", 0)
    # 清理残留数 = 应用卸载/残留清理中, 除"应用本体"以外的条目数
    residues_cleared = 0
    for r in ok_records:
        if r.get("source") not in ("app_uninstall", "orphan_residue"):
            continue
        for it in r.get("items") or []:
            if it.get("category") != "应用本体":  # 无分类的普通残留同样计入
                residues_cleared += 1
        # items 可能被截断, 截断时用 count 兜底(至少记为已清理)
        if r.get("items_truncated"):
            residues_cleared += max(0, int(r.get("count") or 0) - _MAX_ITEMS)

    return {
        "operations": len(ok_records),
        "items_deleted": sum(int(r.get("count") or 0) for r in ok_records),
        "bytes_freed": sum(int(r.get("bytes") or 0) for r in ok_records),
        "apps_uninstalled": apps_uninstalled,
        "residues_cleared": residues_cleared,
        "by_source": by_source,
        "records_total": len(records),
    }


def clear() -> int:
    """清空全部历史(返回被清空的记录数)"""
    with _lock:
        n = len(_load()["records"])
        _save(_empty())
    return n


def app_evidence() -> tuple:
    """从历史提取"应用已被卸载"的证据, 供无主残留扫描使用

    返回 (bundle_ids:set, names:set): 该应用的 Bundle ID 与全部已知名称
    (卸载时应用已不存在, 这些名称是判定其残留的唯一线索)。
    """
    with _lock:
        records = _load()["records"]
    bundle_ids, names = set(), set()
    for r in records:
        if r.get("source") != "app_uninstall" or not r.get("ok"):
            continue
        app = r.get("app") or {}
        bid = (app.get("bundle_id") or "").strip()
        if bid:
            bundle_ids.add(bid.lower())
        for nm in ([app.get("name")] + list(app.get("aliases") or [])):
            nm = (nm or "").strip()
            if nm and len(nm) >= 3:
                names.add(nm)
    return bundle_ids, names


def file_evidence() -> set:
    """从历史提取"已删除文件"的基名(供残留追踪的疑似层使用)"""
    with _lock:
        records = _load()["records"]
    stems = set()
    for r in records:
        if r.get("source") != "file_scan" or not r.get("ok"):
            continue
        for it in r.get("items") or []:
            base = os.path.basename((it.get("path") or "").rstrip("/"))
            if len(base) >= 4:
                stems.add(base.lower())
            stem, _ext = os.path.splitext(base)  # MyBigCache.zip → mybigcache
            if len(stem) >= 4:
                stems.add(stem.lower())
    return stems
