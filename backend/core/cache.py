# coding: utf-8
"""
缓存后端抽象（阶段四）。

对外统一接口 get/set/delete/invalidate_prefix，屏蔽后端差异：

- 默认后端 LocalLRUCache：进程内 LRU（容量上限 + TTL），零依赖，
  适配**默认单 worker 部署**（本系统 uvicorn 单进程，见 README 部署说明）；
- 可选后端 RedisCache：仅当 .env 配置了 REDIS_URL 时启用，
  使多 worker / 多实例共享缓存（同时为限流、令牌黑名单的跨进程共享铺路）。
  redis 为**可选依赖**（pip install "redis[hiredis]"，纯 Python 实现亦可），
  未安装或连接失败时告警并自动降级为本地 LRU，保证零强制新增依赖。

局限：本地 LRU 为进程内实现，多 worker 部署时各进程缓存相互独立、
不保证跨进程一致性（启用 REDIS_URL 即消除此限制）。
"""

import json
import logging
import threading
import time
from collections import OrderedDict

from core.config import (
    CACHE_MAX_ENTRIES,
    CACHE_DEFAULT_TTL,
    REDIS_URL,
)

logger = logging.getLogger(__name__)

# 「未命中」在 get 中以 None 表示；调用方约定不缓存 None 值（避免与未命中混淆）。


class CacheBackend:
    """缓存后端抽象基类"""

    def get(self, key: str):
        raise NotImplementedError

    def set(self, key: str, value, ttl: int = None):
        raise NotImplementedError

    def delete(self, key: str):
        raise NotImplementedError

    def invalidate_prefix(self, prefix: str):
        raise NotImplementedError


class LocalLRUCache(CacheBackend):
    """进程内 LRU 缓存（有序字典 + 每键过期时间戳），线程安全。

    - 容量上限 max_entries，超出时淘汰最久未使用项（OrderedDict 尾部为最近使用）；
    - 每个条目携带独立 TTL，读取时惰性判定过期；
    - 缓存值直接持有对象引用（本地后端不做序列化）。
    """

    def __init__(self, max_entries: int = None, default_ttl: int = None):
        self._max = max_entries or CACHE_MAX_ENTRIES
        self._ttl = default_ttl or CACHE_DEFAULT_TTL
        self._data = OrderedDict()  # key -> (value, expire_ts)
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expire_ts = item
            if time.time() >= expire_ts:
                # 惰性过期
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)  # 命中即置为最近使用
            return value

    def set(self, key: str, value, ttl: int = None):
        if value is None:
            return  # 约定不缓存 None（与「未命中」语义区分）
        with self._lock:
            # 注意用 `is None` 判断：ttl=0 应表示「立即过期」而非回退默认值
            expire_ts = time.time() + (self._ttl if ttl is None else ttl)
            if key in self._data:
                self._data[key] = (value, expire_ts)
                self._data.move_to_end(key)
            else:
                self._data[key] = (value, expire_ts)
                if len(self._data) > self._max:
                    self._data.popitem(last=False)  # 淘汰最久未使用

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        with self._lock:
            for k in [k for k in self._data if k.startswith(prefix)]:
                self._data.pop(k, None)


class RedisCache(CacheBackend):
    """Redis 后端（值以 JSON 序列化，键级 TTL，前缀失效用 SCAN）。

    仅在 REDIS_URL 配置且 redis 可导入、连接成功时使用。
    连接错误时抛异常，由工厂捕获后降级为本地 LRU。
    """

    def __init__(self, url: str):
        import redis  # 懒导入：未安装时不影响本地模式启动

        self._r = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        # 立即 ping 探活，失败即抛（工厂据此降级）
        self._r.ping()

    def get(self, key: str):
        try:
            raw = self._r.get(key)
        except Exception as e:  # noqa: BLE001 运行期 Redis 故障不应冒泡到请求链路
            logger.warning("Redis get 失败（降级为未命中）: %s", e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    def set(self, key: str, value, ttl: int = None):
        if value is None:
            return
        try:
            self._r.set(key, json.dumps(value, ensure_ascii=False),
                        ex=(CACHE_DEFAULT_TTL if ttl is None else ttl))
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis set 失败（本次不缓存）: %s", e)

    def delete(self, key: str):
        try:
            self._r.delete(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis delete 失败: %s", e)

    def invalidate_prefix(self, prefix: str):
        try:
            # 用 SCAN 而非 KEYS，避免大 keyspace 阻塞；批次适度
            for k in self._r.scan_iter(match=f"{prefix}*", count=200):
                self._r.delete(k)
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 前缀失效失败: %s", e)


# ========== 全局单例与工厂 ==========
_cache_instance = None
_factory_lock = threading.Lock()


def get_cache() -> CacheBackend:
    """返回全局缓存实例：配了 REDIS_URL 且可用则 Redis，否则本地 LRU。

    初始化只发生一次（双检锁）；Redis 不可用时永久降级本地并告警一次，
    避免每次调用反复尝试连接。
    """
    global _cache_instance
    if _cache_instance is not None:
        return _cache_instance
    with _factory_lock:
        if _cache_instance is not None:
            return _cache_instance
        if REDIS_URL:
            try:
                _cache_instance = RedisCache(REDIS_URL)
                logger.info("缓存后端：Redis（多实例共享）已启用")
                return _cache_instance
            except Exception as e:  # noqa: BLE001 redis 未安装 / 连接失败均降级
                logger.warning(
                    "已配置 REDIS_URL 但 Redis 不可用（%s: %s），降级为进程内 LRU；"
                    "如需多 worker 共享缓存请安装依赖并确认连通性：pip install \"redis[hiredis]\"",
                    type(e).__name__, e,
                )
        _cache_instance = LocalLRUCache()
        logger.info("缓存后端：本地 LRU（max=%d, ttl=%ds，默认单 worker 部署）",
                    CACHE_MAX_ENTRIES, CACHE_DEFAULT_TTL)
        return _cache_instance


# ========== 面向业务的高层封装 ==========

def entity_cache_key(name: str) -> str:
    """实体详情缓存键（以规范名/命中名为准）"""
    return f"kg:entity:{name}"


def invalidate_entity(name: str):
    """按实体精准失效（图谱数据经脚本更新后可调用）"""
    get_cache().delete(entity_cache_key(name))


def invalidate_user_caches(username: str):
    """批量失效某用户名下缓存（档案/数据更新时即时调用，清除任何用户维度缓存）。

    当前用户数据为内存直读、暂不落缓存；本函数为「个性化/按用户」缓存点预留
    统一失效入口，保证将来新增用户维度缓存时档案更新即时无需逐处改动。
    """
    get_cache().invalidate_prefix(f"user:{username}:")
