# coding: utf-8
"""
Neo4j 连接与 Cypher 执行工具（阶段三从 app.py 抽取，供全管线复用）。

提供三种执行方式：
- run_cypher_sync:  同步执行（启动自检、索引维护等场景）
- run_cypher:       异步包装（线程池执行，不阻塞事件循环）
- run_readonly:     只读事务 + 超时控制（Text2Cypher 等不可信查询专用）
"""

import asyncio
import logging
import time

from neo4j import GraphDatabase, READ_ACCESS

from core.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, SLOW_QUERY_MS

logger = logging.getLogger(__name__)


def _maybe_log_slow(elapsed_ms: float, query: str):
    """统一慢查询埋点：执行耗时超过阈值（SLOW_QUERY_MS）记 WARNING。

    折叠空白并截断查询文本，避免日志被超长语句刷屏；用于定位性能热点。
    """
    if elapsed_ms >= SLOW_QUERY_MS:
        compact = " ".join(query.split())
        logger.warning("慢查询 %.0fms（阈值 %dms）: %.160s", elapsed_ms, SLOW_QUERY_MS, compact)

# 驱动单例：与原 app.py 的连接参数保持一致（连接池上限 50、获取超时 30 秒）
_driver = None


def get_driver():
    """惰性创建并返回全局 Neo4j 驱动单例"""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
            max_connection_pool_size=50,
            connection_acquisition_timeout=30,
        )
    return _driver


def close_driver():
    """关闭驱动（应用生命周期结束时调用）"""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def run_cypher_sync(query: str, parameters: dict = None):
    """同步执行 Cypher 并物化为字典列表（结果集在会话关闭前消费完毕）

    统一计时埋点：耗时超过 SLOW_QUERY_MS 记慢查询日志（阶段四）。
    """
    d = get_driver()
    start = time.perf_counter()
    with d.session() as session:
        result = session.run(query, parameters or {})
        rows = [dict(record) for record in result]
    _maybe_log_slow((time.perf_counter() - start) * 1000, query)
    return rows


async def run_cypher(query: str, parameters: dict = None):
    """异步执行 Cypher：阻塞的数据库调用放入线程池，不阻塞事件循环"""
    return await asyncio.to_thread(run_cypher_sync, query, parameters)


async def run_readonly(query: str, parameters: dict = None, timeout: float = 10.0):
    """只读事务执行（带服务端超时）：

    用于执行来源不完全可信的查询（如 Text2Cypher 生成、经白名单校验后的语句）。
    通过 begin_transaction(timeout=...) 下发事务级超时，超时后服务端中止该事务，
    避免恶意/失控查询拖垮数据库。
    注意：不能用 session.execute_read(_work, timeout=...)，该版本的驱动会将
    kwargs 透传给事务函数而非拦截为事务配置，导致事务函数收到意外的 timeout 参数。
    """
    d = get_driver()

    def _session_work():
        # 以 Neo4j 只读访问模式开会话：即便 Cypher 白名单校验漏网，
        # 服务端也会拒绝任何写子句（纵深防御），配合事务超时双重兜底
        start = time.perf_counter()
        with d.session(default_access_mode=READ_ACCESS) as session:
            tx = session.begin_transaction(timeout=timeout)
            try:
                result = tx.run(query, parameters or {})
                rows = [dict(record) for record in result]
                tx.commit()
                return rows
            except Exception:
                tx.rollback()
                raise
            finally:
                _maybe_log_slow((time.perf_counter() - start) * 1000, query)

    return await asyncio.to_thread(_session_work)
