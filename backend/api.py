from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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


# TODO: 这个映射表,也可以在服务器上进行维护
BASE_PATHS = {
    'system': '/',
    'user': os.path.expanduser('~'),
    'wechat': '~/Library/Containers/com.tencent.xinWeChat/Data',
    'photos': '~/Pictures',
    'yarn': '~/Library/Caches/Yarn',
    'jetbrains': '~/Library/Caches/JetBrains',
    'lark': '~/Library/Caches/LarkShell',
    'pip': '~/Library/Caches/pip',  # FIXME: 这里的PIP可能需要特殊处理, 因为Anaconda的情况下可能存在多个缓存目录
    'google': '~/Library/Caches/Google'
}


def get_base_path(path_type: str) -> str:
    return os.path.expanduser(BASE_PATHS.get(path_type, ''))


@app.get("/api/scan")
async def scan_files(min_size: int = 10, path: str = "wechat"):
    # 如果已经在扫描，返回错误
    if scanner.scanning:
        raise HTTPException(status_code=400, detail="Scan already in progress")
    try:
        if path == "wechat":
            # scanner.scan_message_files(min_size_mb=min_size)
            scanner.start_scan(BASE_PATHS[path], min_size_mb=min_size)
        elif path == "photos":
            scanner.scan_photos_library(min_size_mb=min_size)
        elif path in ["user", "system", "photos", "yarn", "jetbrains", "lark", "google", "pip"]:
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


@app.get("/api/scan/files")
async def get_current_objects():
    return {
        "files": scanner.get_scanned_objects(),
        "total_size": sum(f["size"] for f in scanner.large_files)
    }
