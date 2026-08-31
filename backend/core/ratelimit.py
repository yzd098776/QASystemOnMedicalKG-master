"""
内存滑动窗口限流模块（零外部依赖，不引入 slowapi/redis）。

- 键为 (维度, ip) 或 (维度, username)，各维度阈值由 .env 配置；
- 超限时抛出 429，中文提示「操作过于频繁，请稍后再试」，响应头携带 Retry-After（秒）；
- 使用 threading.Lock 保护窗口字典（FastAPI 路由可能并发执行，含线程池调度）；
- 每次检查时惰性清理过期窗口，避免内存无限增长。
"""

import threading
import time
from collections import deque

from fastapi import HTTPException

# 各 (维度, 身份) 的请求时间戳队列
_WINDOWS: dict = {}
_LOCK = threading.Lock()

# 默认窗口长度：60 秒滑动窗口
_WINDOW_SECONDS = 60


def check_rate_limit(dimension: str, identity: str, limit: int,
                     window_seconds: int = _WINDOW_SECONDS):
    """滑动窗口限流检查。

    :param dimension: 限流维度名（如 "auth-ip" / "auth-user" / "chat-ip" / "chat-user"）
    :param identity: 该维度下的身份标识（客户端 IP 或用户名）
    :param limit: 窗口内允许的最大请求数
    :param window_seconds: 窗口长度（秒）
    超限抛出 HTTPException(429)，携带 Retry-After 响应头。
    """
    if limit <= 0:
        return
    key = (dimension, identity or "anonymous")
    now = time.monotonic()
    with _LOCK:
        # 惰性清理：移除长期无活动的窗口，防止字典无限增长
        stale = [
            k for k, dq in _WINDOWS.items()
            if not dq or (now - dq[-1]) > window_seconds
        ]
        for k in stale:
            del _WINDOWS[k]

        window = _WINDOWS.setdefault(key, deque())
        # 滑出窗口外的旧请求时间戳
        while window and window[0] <= now - window_seconds:
            window.popleft()

        if len(window) >= limit:
            # 最早一次请求滑出窗口的剩余时间（向上取整）
            retry_after = int(window[0] + window_seconds - now) + 1
            raise HTTPException(
                status_code=429,
                detail="操作过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )
        window.append(now)
