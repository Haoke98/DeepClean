from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from typing_extensions import deprecated

from core import FileScanner
import os
import subprocess
from pydantic import BaseModel
import shutil
import psutil
import logging
from utils import logger
from exceptions import DirectoryNotExistError
import ai_service
import json

logger.init('DeepClean-api', console_level=logging.DEBUG)
app = FastAPI()

# CORS设置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

scanner = FileScanner()


class FileAction(BaseModel):
    file_path: str
    base_path: str = ""


class AIAnalyzeRequest(BaseModel):
    file_path: str
    file_type: str = ""
    file_size: int = 0
    md5: str = ""
    history: list = []


class AIChatRequest(BaseModel):
    file_path: str
    file_type: str = ""
    file_size: int = 0
    md5: str = ""
    question: str
    history: list = []


class AIConfigRequest(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout: int = 60


# TODO: 这个映射表,也可以在服务器上进行维护
BASE_PATHS = {
    'user': os.path.expanduser('~'),
    'wechat': '~/Library/Containers/com.tencent.xinWeChat/Data',
    'photos': '~/Pictures',
    'yarn': '~/Library/Caches/Yarn',
    'jetbrains': '~/Library/Caches/JetBrains',
    'lark': '~/Library/Caches/LarkShell',
    'pip': '~/Library/Caches/pip',  # FIXME: 这里的PIP可能需要特殊处理, 因为Anaconda的情况下可能存在多个缓存目录
    'google': '~/Library/Caches/Google'
}

# 系统级扫描: 扫一组系统级缓存/日志/临时目录, 避免直接扫整个根目录 '/' (无界且危险)
SYSTEM_SCAN_DIRS = [
    '/Library/Caches',       # 系统级缓存
    '/Library/Logs',         # 系统日志(可能包含大文件)
    '/private/var/tmp',      # 系统临时文件
    '/private/var/folders',  # 用户/进程的临时与沙盒数据
]


def get_base_path(path_type: str) -> str:
    return os.path.expanduser(BASE_PATHS.get(path_type, ''))


@app.get("/api/scan")
async def scan_files(min_size: int = 10, path: str = "wechat"):
    # 如果已经在扫描，返回错误
    if scanner.scanning:
        raise HTTPException(status_code=400, detail="Scan already in progress")
    try:
        if path == "system":
            # 系统级扫描: 多个系统缓存/临时目录(有界且安全), 而非整个根目录 '/'
            scanner.start_multi_scan(SYSTEM_SCAN_DIRS, min_size_mb=min_size)
        elif path == "wechat":
            # scanner.scan_message_files(min_size_mb=min_size)
            scanner.start_scan(BASE_PATHS[path], min_size_mb=min_size)
        elif path == "photos":
            scanner.scan_photos_library(min_size_mb=min_size)
        elif path in ["user", "photos", "yarn", "jetbrains", "lark", "google", "pip"]:
            scanner.start_scan(BASE_PATHS[path], min_size_mb=min_size)
        elif os.path.exists(path):
            scanner.start_scan(path, min_size_mb=min_size)
        else:
            raise HTTPException(status_code=400, detail="Invalid path")
    except DirectoryNotExistError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "status": "scanning",
        "message": "Scan started successfully"
    }


# 前端统一通过 /api 前缀访问后端(后端直接监听 5173, 不走 vite 代理),
# 因此文件列表路由也需要 /api 前缀, 与 /api/scan 等其他路由保持一致
@app.get("/api/files")
@app.get("/files")
async def get_files(sort_by: str = "size", group_by: str = None):
    files = scanner.large_files

    if group_by == "account":
        return scanner.accounts
    elif group_by == "type":
        files_by_type = {}
        for file in files:
            files_by_type.setdefault(file['type'], []).append(file)
        return files_by_type

    return sorted(files, key=lambda x: x[sort_by], reverse=True)


@app.post("/api/file/action/{action}")
async def file_action(action: str, file_data: FileAction):
    # 使用文件的原始完整路径
    full_path = file_data.file_path

    if not os.path.exists(full_path):
        # 试从 scanner 中获取完整路径
        for file in scanner.large_files:
            if file['relative_path'] == file_data.file_path:
                full_path = file['path']
                break

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"File not found: {full_path}")

    if action == "preview":
        subprocess.run(["open", full_path])
    elif action == "reveal":
        subprocess.run(["open", "-R", full_path])
    elif action == "delete":
        if os.path.isdir(full_path):
            # 删除非空目录
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        # 从 scanner 的文件列表中移除已删除的文件
        scanner.large_files = [f for f in scanner.large_files if f['path'] != full_path]

    return {"status": "success"}


@deprecated("该API已被最新的系统状态监控API:monitor所平替,将不再适用!")
@app.get("/api/disk-usage")
async def get_disk_usage():
    total, used, free = shutil.disk_usage("/")
    return {
        "total": total,
        "used": used,
        "free": free,
        "usage_percent": (used / total) * 100
    }


@app.get("/api/monitor")
async def get_system_monitor():
    # 磁盘使用情况
    disk = psutil.disk_usage("/")

    # 内存使用情况
    mem = psutil.virtual_memory()

    # 交换内存使用情况
    swap = psutil.swap_memory()

    return {
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "free": mem.available,
            "percent": mem.percent
        },
        "swap": {
            "total": swap.total,
            "used": swap.used,
            "free": swap.free,
            "percent": swap.percent
        }
    }


@app.get("/api/scan/progress")
async def get_scan_progress():
    return scanner.get_scan_progress()


@app.post("/api/scan/cancel")
async def cancel_scan():
    """请求取消当前扫描"""
    scanner.cancel_scan()
    return {"status": "cancelling"}


@app.post("/api/scan/reset")
async def reset_scan():
    """强制重置扫描状态并清空结果(用于解围卡死的扫描)"""
    scanner.force_reset()
    return {"status": "reset"}


@app.get("/api/scan/files")
async def get_current_objects():
    return {
        "files": scanner.get_scanned_objects(),
        "total_size": sum(f["size"] for f in scanner.large_files)
    }


# ==================== AI 分析 ====================

def _size_human(size) -> str:
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "未知"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _find_file_info(data: dict) -> dict:
    """构造分析用的文件信息(优先用前端传来的字段, 找不到时回退到扫描结果中查找)"""
    file_info = {
        "path": data.get("file_path", ""),
        "type": data.get("file_type") or "未知",
        "size": data.get("file_size") or 0,
        "size_human": _size_human(data.get("file_size")),
        "md5": data.get("md5", ""),
    }
    if not file_info["path"]:
        return file_info
    # 从扫描结果里补全(前端可能只传了 path)
    for f in scanner.large_files:
        if f.get("path") == file_info["path"]:
            file_info["type"] = data.get("file_type") or f.get("type") or "未知"
            file_info["size"] = data.get("file_size") or f.get("size") or 0
            file_info["size_human"] = _size_human(file_info["size"])
            file_info["md5"] = data.get("md5") or f.get("md5") or ""
            break
    return file_info


@app.get("/api/ai/config")
async def get_ai_config():
    """获取 AI 配置(api_key 脱敏回显)"""
    config = ai_service.load_ai_config()
    return {
        "config": ai_service.mask_config(config),
        "configured": ai_service.is_configured(config),
        "has_api_key": bool(config.get("api_key")),
    }


@app.post("/api/ai/config")
async def update_ai_config(req: AIConfigRequest):
    """更新并持久化 AI 配置"""
    # api_key 若为掩码/空值, 保留原值不覆盖
    current = ai_service.load_ai_config()
    api_key = req.api_key
    if not api_key or "****" in api_key:
        api_key = current.get("api_key", "")

    config = {
        "base_url": req.base_url.strip(),
        "api_key": api_key.strip(),
        "model": req.model.strip(),
        "temperature": max(0.0, min(2.0, req.temperature)),
        "max_tokens": max(200, min(16000, req.max_tokens)),
        "timeout": max(10, min(300, req.timeout)),
    }
    if not config["base_url"] or not config["model"]:
        raise HTTPException(status_code=400, detail="base_url 和 model 不能为空")
    ai_service.save_ai_config(config)
    return {"status": "success", "config": ai_service.mask_config(config)}


@app.post("/api/ai/test")
async def test_ai_connection():
    """测试 AI 连接: 发送一条最小消息, 返回连通性"""
    config = ai_service.load_ai_config()
    if not ai_service.is_configured(config):
        return {"ok": False, "message": "尚未配置 AI 模型"}
    try:
        reply = ai_service.AIChatClient(config).chat(
            [{"role": "user", "content": "回复: OK"}]
        )
        return {"ok": True, "message": f"连接成功: {reply[:50]}"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {e}"}


@app.post("/api/ai/analyze")
async def ai_analyze(req: AIAnalyzeRequest):
    """AI 分析文件(流式返回 SSE)"""
    file_info = _find_file_info(dict(req))
    history = [m for m in (req.history or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]

    def gen():
        error_msg = "\n\n> ⚠️ 分析失败: {}"
        try:
            for chunk in ai_service.analyze_file_stream(file_info, history):
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logging.error(f"AI 分析异常: {e}")
            yield f"data: {json.dumps({'content': error_msg.format(e)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/ai/chat")
async def ai_chat(req: AIChatRequest):
    """基于文件的 AI 对话(流式返回 SSE)"""
    file_info = _find_file_info(dict(req))
    history = [m for m in (req.history or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]

    def gen():
        error_msg = "\n\n> ⚠️ 对话失败: {}"
        try:
            for chunk in ai_service.chat_about_file_stream(file_info, req.question, history):
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as gen_error:
            logging.error(f"AI 对话异常: {gen_error}")
            yield f"data: {json.dumps({'content': error_msg.format(gen_error)}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/ai/analyze/nonstream")
async def ai_analyze_nonstream(req: AIAnalyzeRequest):
    """AI 分析文件(非流式, 一次性返回; 便于 curl 调试)"""
    file_info = _find_file_info(dict(req))
    history = [m for m in (req.history or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    config = ai_service.load_ai_config()
    if not ai_service.is_configured(config):
        raise HTTPException(status_code=400, detail="尚未配置 AI 模型")
    messages = [{"role": "system", "content": ai_service.SYSTEM_PROMPT},
                {"role": "user", "content": ai_service.build_analysis_prompt(file_info)}]
    try:
        reply = ai_service.AIChatClient(config).chat(messages)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 接口错误: {e}")
    return {"analysis": reply}
