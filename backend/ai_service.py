"""
AI 分析服务
- 配置管理: 从 backend/.env 读取, 支持运行时更新并持久化
- OpenAI 兼容客户端: 适配 火山方舟Ark/OpenAI/DeepSeek/Ollama 等任何
  兼容 /chat/completions 的服务(通过 base_url + api_key + model 配置)
- 文件分析提示词: 输出结构化分析(来源/用途/删除评估/处理建议)
"""
import os
import json
import logging
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent
ENV_FILE = BACKEND_DIR / ".env"

# 默认配置: 未配置时使用本地 Ollama, 引导用户在设置里填写真实模型
DEFAULT_CONFIG = {
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "",
    "model": "ep-20260602132217-br66t",
    "temperature": 0.3,
    "max_tokens": 2000,
    "timeout": 60,
}


def load_ai_config() -> dict:
    """读取 AI 配置: backend/.env 优先, 不存在时用默认值"""
    config = dict(DEFAULT_CONFIG)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key in config:
                if key in ("temperature",):
                    try:
                        config[key] = float(value)
                    except ValueError:
                        pass
                elif key in ("max_tokens", "timeout"):
                    try:
                        config[key] = int(value)
                    except ValueError:
                        pass
                else:
                    config[key] = value
    return config


def save_ai_config(config: dict) -> None:
    """保存 AI 配置到 backend/.env (持久化)"""
    lines = ["# DeepClean AI 分析服务配置"]
    for key in ("base_url", "api_key", "model", "temperature", "max_tokens", "timeout"):
        if key in config:
            lines.append(f"{key}={config[key]}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask_config(config: dict) -> dict:
    """脱敏配置(用于回显给前端, 避免 API Key 明文暴露)"""
    masked = dict(config)
    key = str(masked.get("api_key", ""))
    if key:
        masked["api_key"] = key[:6] + "****" + key[-4:] if len(key) > 12 else "****"
    return masked


def is_configured(config: dict) -> bool:
    """是否已配置到可用状态(base_url+model 存在; key 可为空-本地 Ollama 等无需鉴权)"""
    return bool(config.get("base_url") and config.get("model"))


ANALYSIS_PROMPT = """你是一名 macOS 系统存储与磁盘清理专家。请分析以下文件/文件夹, 用简体中文回答。

文件信息:
- 路径: {path}
- 类型: {type}
- 大小: {size}
{extra_info}

请严格按照以下 Markdown 结构输出分析(保持四个二级标题, 不要输出其他内容):
## 1. 这个数据是怎么产生的
(判断来源: 哪个软件/系统组件/开发工具写入的, 什么场景下产生)
## 2. 用来干嘛的？有什么用？
(解释用途, 删除后对系统和软件的影响)
## 3. 该不该删？有没有办法压缩？
(给出删除风险评估: 安全/谨慎/危险 三档, 以及压缩或迁移的建议)
## 4. 处理建议
(给出具体可操作步骤, 如果建议删除请说明删除后如何重建/恢复)
"""


def build_analysis_prompt(file_info: dict) -> str:
    """构建文件分析提示词"""
    extra = []
    if file_info.get("md5"):
        extra.append(f"- MD5: {file_info['md5']}")
    if file_info.get("account"):
        extra.append(f"- 微信账号目录: {file_info['account']}")
    extra_info = "\n".join(extra)
    return ANALYSIS_PROMPT.format(
        path=file_info.get("path", "未知"),
        type=file_info.get("type", "未知"),
        size=file_info.get("size_human", file_info.get("size", "未知")),
        extra_info=extra_info,
    )


SYSTEM_PROMPT = """你是 DeepClean 文件清理助手, 一名 macOS 存储与磁盘清理专家。
- 回答用简体中文, 简洁准确, 使用 Markdown 格式
- 涉及删除文件时, 优先提示备份与风险评估
- 你此前的分析对象是同一个文件, 用户会基于该分析继续追问, 保持上下文一致"""


def chat_url(base_url: str) -> str:
    """拼接 chat completions 接口地址"""
    base = str(base_url).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


class AIChatClient:
    """OpenAI 兼容聊天客户端(支持流式与非流式)"""

    def __init__(self, config: dict):
        self.config = config

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        return headers

    def _payload(self, messages: list, stream: bool = False):
        return {
            "model": self.config["model"],
            "messages": messages,
            "temperature": self.config.get("temperature", 0.3),
            "max_tokens": self.config.get("max_tokens", 2000),
            "stream": stream,
        }

    def chat(self, messages: list) -> str:
        """非流式: 一次性返回完整回复"""
        url = chat_url(self.config["base_url"])
        with httpx.Client(timeout=self.config.get("timeout", 60)) as client:
            resp = client.post(url, json=self._payload(messages), headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def chat_stream(self, messages: list):
        """流式: 逐块 yield 文本增量, 出错时 yield 错误标记"""
        url = chat_url(self.config["base_url"])
        try:
            with httpx.Client(timeout=self.config.get("timeout", 60)) as client:
                with client.stream(
                    "POST", url, json=self._payload(messages, stream=True), headers=self._headers()
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = e.response.text[:300]
            except Exception:
                pass
            logging.error(f"AI 接口 HTTP 错误: {e.response.status_code} {body}")
            yield f"\n\n> ⚠️ AI 接口返回错误 {e.response.status_code}: {body}"
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
            logging.error(f"AI 接口连接失败: {e}")
            yield f"\n\n> ⚠️ 无法连接 AI 服务: {e}"


def analyze_file_stream(file_info: dict, history: list | None = None):
    """
    流式分析文件: 首次分析带结构化提示词, 追问时基于历史上下文
    history: [{"role": "user"|"assistant", "content": "..."}, ...] 最近的对话历史
    """
    config = load_ai_config()
    if not is_configured(config):
        yield "> ⚠️ 尚未配置 AI 模型, 请点击右上角「AI 设置」完成配置"
        return

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        # 追问: 携带首次分析原文与后续历史
        messages.extend(history[-20:])
        messages.append({"role": "user", "content": "请继续基于上述分析回答我下一个问题(见下一条消息)"})
        messages.append({"role": "user", "content": build_analysis_prompt(file_info)})
    else:
        messages.append({"role": "user", "content": build_analysis_prompt(file_info)})

    client = AIChatClient(config)
    yield from client.chat_stream(messages)


def chat_about_file_stream(file_info: dict, question: str, history: list | None = None):
    """基于文件的自由对话(用户在分析结果上继续追问)"""
    config = load_ai_config()
    if not is_configured(config):
        yield "> ⚠️ 尚未配置 AI 模型, 请点击右上角「AI 设置」完成配置"
        return

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # 附带文件上下文
    context = f"当前讨论的文件: 路径={file_info.get('path')}, 类型={file_info.get('type')}, 大小={file_info.get('size_human', file_info.get('size'))}"
    messages.append({"role": "system", "content": context})
    if history:
        messages.extend(history[-20:])
    messages.append({"role": "user", "content": question})

    client = AIChatClient(config)
    yield from client.chat_stream(messages)
