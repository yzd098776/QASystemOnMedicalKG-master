# coding: utf-8
"""
DeepSeek 调用共享封装（阶段三）。

原 app.py 中 chat / prevention / chronic 三处各自持有一份 httpx 调用代码；
本阶段仅要求 chat 路径走 GraphRAG 管线，故把 chat 的流式逻辑与
Text2Cypher 所需的一次性补全调用抽取为本模块的两个共享函数，
行为与原实现保持一致（超时 60 秒/连接 10 秒、deepseek-chat 模型）。
prevention / chronic 两处按任务要求不动。
"""

import json
import logging

import httpx

from core.config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用失败（网络异常或非 200 响应），由调用方负责降级"""


def has_api_key() -> bool:
    """是否配置了 DeepSeek API 密钥（未配置时各调用方走图谱降级路径）"""
    return bool(DEEPSEEK_API_KEY)


async def stream_chat_completion(messages, temperature: float = 0.7, max_tokens: int = 2000):
    """流式调用 DeepSeek，异步生成器逐段产出文本片段。

    与既有 /api/chat 行为一致：stream=True 解析 SSE 行，逐块取
    choices[0].delta.content；非 200 或网络异常抛 LLMError，
    由管线统一降级为用户可见的友好提示帧。
    """
    if not DEEPSEEK_API_KEY:
        raise LLMError("DEEPSEEK_API_KEY 未配置")
    logger.info("调用 DeepSeek API（流式）")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        async with client.stream(
            "POST",
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "stream": True,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        ) as response:
            if response.status_code != 200:
                raise LLMError(f"DeepSeek API 返回 {response.status_code}")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, IndexError, KeyError):
                    # 单行解析失败不影响整体流式输出（与既有行为一致）
                    continue


async def complete_chat(messages, temperature: float = 0.3, max_tokens: int = 2000):
    """一次性（非流式）补全调用，返回完整文本；失败抛 LLMError。

    供 Text2Cypher 等需要完整结构化输出的场景使用。
    """
    if not DEEPSEEK_API_KEY:
        raise LLMError("DEEPSEEK_API_KEY 未配置")
    logger.info("调用 DeepSeek API（一次性补全）")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        resp = await client.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        if resp.status_code != 200:
            raise LLMError(f"DeepSeek API 返回 {resp.status_code}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]
