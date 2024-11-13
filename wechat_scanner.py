import re
import os
import click
from pathlib import Path
from tools import storageFormat


class WeChatScanner:
    def __init__(self):
        self.base_path = os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat")
        self.large_files = []
        self.size_threshold = 10 * 1024 * 1024  # 默认10MB
        self.accounts = {}

    def _is_account_dir(self, dirname):
        """检查是否为账号目录（32位十六进制字符串）"""
        pattern = r'^[a-fA-F0-9]{32}$'
        return bool(re.match(pattern, dirname))

    def scan_message_files(self, min_size_mb=10):
        """扫描微信消息中的大文件"""
        self.size_threshold = min_size_mb * 1024 * 1024

        # 查找版本目录（如 2.0b4.0.9）
        version_dirs = [d for d in os.listdir(self.base_path) 
                       if os.path.isdir(os.path.join(self.base_path, d)) and d.startswith('2.')]
        if not version_dirs:
            click.echo("未找到微信版本目录")
            return

        version_path = os.path.join(self.base_path, version_dirs[0])

        # 目标文件夹模式
        target_patterns = [
            "Message/MessageTemp",
            "Message/MessageData",
            "Message/Media"
        ]

        # 只遍历账号目录（32位十六进制字符串格式的目录）
        account_dirs = [d for d in os.listdir(version_path) 
                       if os.path.isdir(os.path.join(version_path, d)) and self._is_account_dir(d)]

        if not account_dirs:
            click.echo("未找到微信账号目录")
            return

        click.echo(f"找到 {len(account_dirs)} 个微信账号目录")

        # 遍历每个账号目录
        for account_dir in account_dirs:
            account_path = os.path.join(version_path, account_dir)
            self.accounts[account_dir] = {"files": [], "total_size": 0}

            # 在每个账号目录下搜索目标文件夹
            for pattern in target_patterns:
                pattern_path = os.path.join(account_path, pattern)
                if not os.path.exists(pattern_path):
                    continue

                for root, _, files in os.walk(pattern_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            file_size = os.path.getsize(file_path)
                            if file_size >= self.size_threshold:
                                file_type = self._get_file_type(file)
                                relative_path = os.path.relpath(file_path, account_path)
                                file_info = {
                                    'path': file_path,
                                    'relative_path': relative_path,
                                    'size': file_size,
                                    'type': file_type,
                                    'account': account_dir
                                }
                                self.large_files.append(file_info)
                                self.accounts[account_dir]["files"].append(file_info)
                                self.accounts[account_dir]["total_size"] += file_size
                        except (OSError, IOError):
                            continue

        # 按文件大小排序
        self.large_files.sort(key=lambda x: x['size'], reverse=True)

    def _get_file_type(self, filename):
        """获取文件类型"""
        ext = os.path.splitext(filename)[1].lower()
        type_map = {
            '.jpg': '图片',
            '.jpeg': '图片',
            '.png': '图片',
            '.gif': '图片',
            '.mp4': '视频',
            '.mov': '视频',
            '.mp3': '音频',
            '.wav': '音频',
            '.pdf': 'PDF文档',
            '.doc': 'Word文档',
            '.docx': 'Word文档',
            '.xls': 'Excel表格',
            '.xlsx': 'Excel表格',
        }
        return type_map.get(ext, '其他')

    def print_large_files(self):
        """打印大文件列表"""
        if not self.large_files:
            click.echo("未发现大文件!")
            return

        click.echo("\n=== 微信大文件列表 ===")
        total_size = sum(account["total_size"] for account in self.accounts.values())
        click.echo(f"总计发现 {len(self.large_files)} 个大文件，总大小: {storageFormat(total_size)}")

        # 按账号显示统计信息
        for account, info in self.accounts.items():
            if info["files"]:
                click.echo(f"\n\n账号目录: {account}")
                click.echo(f"文件数量: {len(info['files'])}")
                click.echo(f"占用空间: {storageFormat(info['total_size'])}")

                # 按类型分组显示文件
                files_by_type = {}
                for file in info["files"]:
                    files_by_type.setdefault(file['type'], []).append(file)

                for file_type, files in files_by_type.items():
                    type_size = sum(f['size'] for f in files)
                    click.echo(f"\n  {file_type}文件 ({len(files)}个, 共{storageFormat(type_size)}):")
                    for idx, file in enumerate(sorted(files, key=lambda x: x['size'], reverse=True), 1):
                        click.echo(f"    {idx}. 大小: {storageFormat(file['size'])}")
                        click.echo(f"       位置: {file['relative_path']}")

    def clean_selected_files(self):
        """清理选中的文件"""
        if not self.large_files:
            return

        click.echo("\n请选择要清理的文件编号（多个文件用逗号分隔，直接回车取消）：")
        choice = click.prompt("输入编号", default="", show_default=False)

        if not choice:
            return

        try:
            selected_indices = [int(x.strip()) for x in choice.split(",")]
            for idx in selected_indices:
                if 1 <= idx <= len(self.large_files):
                    file_info = self.large_files[idx - 1]
                    if click.confirm(f"确定要删除 {file_info['path']} ({storageFormat(file_info['size'])})？"):
                        try:
                            os.remove(file_info['path'])
                            click.echo(f"已删除: {file_info['path']}")
                        except OSError as e:
                            click.echo(f"删除失败: {e}")
        except ValueError:
            click.echo("输入格式错误！")
