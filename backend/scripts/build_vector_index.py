# coding: utf-8
"""
向量索引构建与填充脚本（阶段三 3.3，幂等、可重复执行）。

功能：
1. 为 Disease/Drug/Symptom 三类节点创建 Neo4j 5.26 原生 vector index
   （CREATE VECTOR INDEX IF NOT EXISTS，维度与相似度函数与写入一致）；
2. 逐节点取 name + desc 生成纯 Python 哈希字符 n-gram 嵌入，
   写入节点属性 embedding（分批提交，默认每批 500 节点）；
3. 4.4 万节点分批执行，可用 --limit 试跑小批量；
4. 结束时等待全部向量索引上线并输出统计。

选型结论与局限、升级路径见 services/vector_index.py 模块头注释与
项目 README「向量检索方案」一节。

用法（在 backend 目录下）：
    python scripts/build_vector_index.py            # 全量填充
    python scripts/build_vector_index.py --limit 100  # 试跑：每类标签最多 100 个节点
"""

import argparse
import logging
import os
import sys
import time

# 使本脚本可从项目任意位置运行：把 backend 目录加入模块搜索路径，
# 复用 core.config 的 .env 加载与安全校验
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from neo4j import GraphDatabase  # noqa: E402

from core.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, EMBEDDING_DIM  # noqa: E402
from services.vector_index import (  # noqa: E402
    VECTOR_LABELS,
    embed,
    ensure_vector_indexes,
    index_name_for,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_vector_index")

# 单批写入节点数（分批事务，避免超大事务；4.4 万节点约 90 批）
BATCH_SIZE = 500


def _run(session, query, params=None):
    result = session.run(query, params or {})
    return [dict(r) for r in result]


def build(label: str, session, limit=None) -> int:
    """为单个标签的节点生成并写入嵌入，返回写入节点数。

    幂等：重复执行只是用相同确定性嵌入覆写（同一文本恒得同一向量）。
    """
    read_q = f"MATCH (n:{label}) RETURN n.name AS name, n.desc AS desc"
    if limit is not None:
        read_q += f" LIMIT {int(limit)}"

    write_q = (
        f"UNWIND $batch AS row "
        f"MATCH (n:{label} {{name: row.name}}) "
        f"SET n.embedding = row.emb"
    )

    start = time.time()
    total = 0
    batch = []
    for record in session.run(read_q).data():
        name = record.get("name")
        if not name:
            continue
        # name + desc 拼接作为嵌入语料（desc 缺失时仅用 name）
        text = f"{name} {record.get('desc') or ''}"
        batch.append({"name": name, "emb": embed(text)})
        if len(batch) >= BATCH_SIZE:
            session.run(write_q, {"batch": batch})
            total += len(batch)
            batch = []
            if total % 5000 < BATCH_SIZE:
                logger.info("[%s] 已写入 %d 个节点（%.1f 秒）", label, total, time.time() - start)
    if batch:
        session.run(write_q, {"batch": batch})
        total += len(batch)
    logger.info("[%s] 完成：共写入 %d 个节点，耗时 %.1f 秒", label, total, time.time() - start)
    return total


def main():
    parser = argparse.ArgumentParser(description="构建并填充医疗图谱向量索引")
    parser.add_argument("--limit", type=int, default=None,
                        help="试跑模式：每类标签最多处理的节点数（默认全量）")
    args = parser.parse_args()

    logger.info("连接 Neo4j: %s（嵌入维度 %d）", NEO4J_URI, EMBEDDING_DIM)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            # 1. 幂等创建向量索引（内部等待索引上线）
            ensure_vector_indexes(lambda q, p=None: _run(session, q, p))
            # 2. 逐标签填充嵌入
            grand = 0
            for label in VECTOR_LABELS:
                grand += build(label, session, limit=args.limit)
            logger.info("全部完成：共写入 %d 个节点的 embedding 属性", grand)
            # 3. 结束后再次确认索引在线（填充期间索引持续增量更新）
            rows = _run(session, "SHOW INDEXES YIELD name, state RETURN name, state")
            expected = {index_name_for(label) for label in VECTOR_LABELS}
            for r in rows:
                if r.get("name") in expected:
                    logger.info("索引 %s 状态: %s", r["name"], r.get("state"))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
