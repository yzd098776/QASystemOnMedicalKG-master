# coding: utf-8
"""
向量索引构建与填充脚本（阶段三建线 + 阶段 B 语义升级）。

双模式（由 .env EMBEDDING_PROVIDER 决定）：
- hash（默认）：行为与升级前逐位一致——name+desc 哈希 n-gram 嵌入直接写 Neo4j；
- zhipu / tongyi（语义模式）：
  1. **MySQL entity_embeddings 是 source of truth**，Neo4j 属性只是同步副本：
     先查库缺哪些实体，只对缺的调远程 API（重建索引时优先读库，避免重复调用烧钱）；
  2. 增量：只算缺的行；MySQL 逐批落库即天然 checkpoint（每 500 条一批），
     断点续跑 = 重新执行本脚本；
  3. --dry-run：打印实体数、预估 tokens 与费用，不发起任何远程调用；
     正式跑前同样预估，超 EMBEDDING_MAX_COST_YUAN 中止并提示（约束 7）；
  4. 嵌入语料仅实体名（C4：先建基线，验证收益后再考虑拼接简介）；
  5. 维度变更自动检测：Neo4j 向量索引维度不可原地修改，检测不一致时
     打印提示并 DROP→CREATE，随后从 MySQL 全量重写副本。

用法（backend 目录下）：
    python scripts/build_vector_index.py --dry-run      # 费用/规模预估（语义模式必跑）
    python scripts/build_vector_index.py --limit 200    # 试跑：每类标签最多 200 实体
    python scripts/build_vector_index.py                # 全量增量
"""

import argparse
import logging
import os
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects.mysql import insert as mysql_insert  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

from core.config import (  # noqa: E402
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, EMBEDDING_MAX_COST_YUAN,
)
from services.vector_index import (  # noqa: E402
    VECTOR_LABELS,
    embed,
    create_index_cypher,
    ensure_vector_indexes,
    index_name_for,
)
from services.embedding_provider import (  # noqa: E402
    get_provider, semantic_mode, vec_to_blob, entity_key_hash,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_vector_index")

# 单批写入节点数（分批事务，避免超大事务；亦为 MySQL checkpoint 粒度）
BATCH_SIZE = 500


def _run(session, query, params=None):
    result = session.run(query, params or {})
    return [dict(r) for r in result]


def _index_dim(session, name: str):
    """读取现存向量索引的维度（不存在返回 None）"""
    try:
        rows = _run(session,
                    "SHOW INDEXES YIELD name, type, options "
                    "RETURN name, type, options")
    except Exception:
        return None
    for r in rows:
        if r.get("name") == name:
            opts = r.get("options") or {}
            cfg = opts.get("indexConfig") or {}
            dim = cfg.get("`vector.dimensions`") or cfg.get("vector.dimensions")
            return int(dim) if dim is not None else None
    return None


def _ensure_indexes_dim_match(session) -> bool:
    """检测现存索引维度与当前生效维度是否一致；不一致则 DROP（需重建）。

    返回 True 表示发生了 DROP（索引与嵌入需全部重建/重写）。
    """
    from services.vector_index import _active_dim
    want = _active_dim()
    dropped = False
    for label in VECTOR_LABELS:
        name = index_name_for(label)
        have = _index_dim(session, name)
        if have is not None and have != want:
            logger.warning("向量索引 %s 维度 %d != 当前生效维度 %d：DROP 后重建"
                           "（Neo4j 不支持改索引维度）", name, have, want)
            _run(session, f"DROP INDEX {name} IF EXISTS")
            dropped = True
    return dropped


# ========== hash 模式：保持阶段三原行为 ==========

def build_hash(label: str, session, limit=None) -> int:
    """为单个标签的节点生成并写入哈希嵌入（name + desc 拼接语料，与升级前一致）。

    幂等：重复执行只是用相同确定性嵌入覆写。
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


# ========== 语义模式 ==========

def _mysql_existing(engine, provider_name: str, model_ver: str) -> set:
    """已入库（同 provider+model_ver+dim）的 key_hash 集合 → 缺什么一目了然"""
    from core import db
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(db.entity_embeddings.c.key_hash)
            .where(db.entity_embeddings.c.provider == provider_name,
                   db.entity_embeddings.c.model_ver == model_ver,
                   db.entity_embeddings.c.dim == provider_dim())
        ).fetchall()
    return {r[0] for r in rows}


def provider_dim() -> int:
    return get_provider().dim


def _collect_entities(session, limit=None):
    """读取三类标签全部实体名（嵌入语料仅实体名，C4）。返回 [(label, name)]"""
    out = []
    for label in VECTOR_LABELS:
        q = f"MATCH (n:{label}) RETURN n.name AS name"
        if limit is not None:
            q += f" LIMIT {int(limit)}"
        for r in session.run(q).data():
            if r.get("name"):
                out.append((label, r["name"]))
    return out


def _mysql_load_vectors(engine, provider_name: str, model_ver: str) -> dict:
    """从 MySQL 读出全部现存向量 {(label, name): [floats]}——重建 Neo4j 副本时优先读库"""
    from core import db
    out = {}
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(db.entity_embeddings.c.label, db.entity_embeddings.c.name,
                      db.entity_embeddings.c.embedding, db.entity_embeddings.c.dim)
            .where(db.entity_embeddings.c.provider == provider_name,
                   db.entity_embeddings.c.model_ver == model_ver)
        ).fetchall()
    from services.embedding_provider import blob_to_vec
    for label, name, blob, dim in rows:
        out[(label, name)] = blob_to_vec(blob, dim)
    return out


def _mysql_store(engine, items):
    """items: [(label, name, vector)]，批量 upsert 进 MySQL（source of truth）"""
    from core import db
    provider = get_provider()
    with engine.begin() as conn:
        for label, name, vec in items:
            stmt = mysql_insert(db.entity_embeddings).values(
                key_hash=entity_key_hash(label, name),
                label=label, name=name,
                provider=provider.name, model_ver=provider.model_ver,
                dim=provider.dim, embedding=vec_to_blob(vec),
            )
            stmt = stmt.on_duplicate_key_update(embedding=stmt.inserted.embedding)
            conn.execute(stmt)


def _sync_neo4j(session, vecs: dict):
    """把 { (label,name): vector } 全量刷入 Neo4j embedding 属性（分批事务）"""
    write_q = (
        "UNWIND $batch AS row "
        f"MATCH (n:{{label}}) WHERE n.name = row.name SET n.embedding = row.emb"
    )
    by_label = {}
    for (label, name), vec in vecs.items():
        by_label.setdefault(label, []).append({"name": name, "emb": vec})
    total = 0
    for label, rows in by_label.items():
        q = write_q.format(label=label)
        for i in range(0, len(rows), BATCH_SIZE):
            session.run(q, {"batch": rows[i:i + BATCH_SIZE]})
            total += min(BATCH_SIZE, len(rows) - i)
    logger.info("Neo4j 副本同步完成：写入 %d 个节点", total)
    return total


def run_semantic(session, engine, args):
    provider = get_provider()
    entities = _collect_entities(session, limit=args.limit)
    logger.info("语义模式：%s/%s dim=%d，本轮范围内实体 %d 个",
                provider.name, provider.model_ver, provider.dim, len(entities))

    # dry-run：规模 + 预估 tokens/费用（不发起任何远程调用，约束 7）
    if args.dry_run:
        texts = [name for _, name in entities]
        cost, tokens = provider.estimate_cost(texts)
        logger.info("[dry-run] 实体总数=%d  预估 tokens≈%d  预估费用≈%.4f 元"
                    "（阈值 EMBEDDING_MAX_COST_YUAN=%.2f 元）",
                    len(entities), tokens, cost, EMBEDDING_MAX_COST_YUAN)
        if cost > EMBEDDING_MAX_COST_YUAN:
            logger.error("[dry-run] 预估费用 %.4f 元 超过阈值 %.2f 元："
                         "中止。可降低范围（--limit）或调高阈值后再试",
                         cost, EMBEDDING_MAX_COST_YUAN)
            sys.exit(1)
        logger.info("[dry-run] 未发起远程调用、未写库。")
        return

    # 正式跑前的费用熔断：以「缺算部分」预估
    existing = _mysql_existing(engine, provider.name, provider.model_ver)
    missing = [(l, n) for (l, n) in entities if entity_key_hash(l, n) not in existing]
    logger.info("MySQL 已有 %d 条，本次需新算 %d 条", len(entities) - len(missing), len(missing))
    if missing:
        est_cost, est_tokens = provider.estimate_cost([n for _, n in missing])
        if est_cost > EMBEDDING_MAX_COST_YUAN:
            logger.error("新算 %d 条预估费用 %.4f 元超过 EMBEDDING_MAX_COST_YUAN=%.2f，"
                         "中止（可先 --dry-run 复核）", len(missing), est_cost, EMBEDDING_MAX_COST_YUAN)
            sys.exit(1)
        logger.info("新算预估：%d tokens ≈ %.4f 元", est_tokens, est_cost)

    # 分批调 API → 每批 500 落 MySQL（即 checkpoint，中断后重跑自动续）
    batch = []
    t0 = time.time()
    for idx, (label, name) in enumerate(missing, 1):
        batch.append((label, name))
        if len(batch) >= provider.batch_size:
            vecs = provider.embed([n for _, n in batch], strict=True)
            _mysql_store(engine, [(l, n, v) for (l, n), v in zip(batch, vecs)])
            batch = []
            if idx % 500 < provider.batch_size:
                logger.info("已嵌入并入库 %d/%d（%.1f 秒）", idx, len(missing), time.time() - t0)
    if batch:
        vecs = provider.embed([n for _, n in batch], strict=True)
        _mysql_store(engine, [(l, n, v) for (l, n), v in zip(batch, vecs)])

    # 全量从 MySQL 读（含历史已有），刷 Neo4j 副本——MySQL 为 source of truth
    scope = set(entities)
    vecs = {k: v for k, v in _mysql_load_vectors(
        engine, provider.name, provider.model_ver).items() if k in scope}
    logger.info("从 MySQL 载入 %d 条向量用于同步 Neo4j 副本", len(vecs))
    _sync_neo4j(session, vecs)


def run_hash(session, args):
    for label in VECTOR_LABELS:
        build_hash(label, session, limit=args.limit)


def main():
    parser = argparse.ArgumentParser(description="构建并填充医疗图谱向量索引")
    parser.add_argument("--limit", type=int, default=None,
                        help="试跑模式：每类标签最多处理的实体数（默认全量）")
    parser.add_argument("--dry-run", action="store_true",
                        help="语义模式费用预估：只报告规模与预估费用，不调 API 不写库")
    args = parser.parse_args()

    provider = get_provider()
    mode = "hash" if not semantic_mode() else provider.name
    if args.dry_run and mode == "hash":
        logger.info("hash 模式零成本，--dry-run 仅输出规模预估：")
    logger.info("连接 Neo4j: %s（provider=%s dim=%d）", NEO4J_URI, mode, provider.dim)

    engine = None
    if mode != "hash":
        # 语义模式需要 MySQL（entity_embeddings 为 source of truth）
        from core import db as core_db
        core_db.init_schema()
        engine = core_db.get_engine()

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            # 1. 维度变更检测：不一致会 DROP 现存索引（提示后自动重建）
            _ensure_indexes_dim_match(session)
            # 2. 幂等创建索引（内部等待上线）
            ensure_vector_indexes(lambda q, p=None: _run(session, q, p))
            # 3. 计算与填充
            if mode == "hash":
                if args.dry_run:
                    for label in VECTOR_LABELS:
                        c = _run(session, f"MATCH (n:{label}) RETURN count(n) AS c")[0]["c"]
                        logger.info("[dry-run] %s: %d 个实体（哈希嵌入零成本）", label, c)
                    return
                run_hash(session, args)
            else:
                run_semantic(session, engine, args)
            # 4. 确认索引在线
            rows = _run(session, "SHOW INDEXES YIELD name, state RETURN name, state")
            expected = {index_name_for(label) for label in VECTOR_LABELS}
            for r in rows:
                if r.get("name") in expected:
                    logger.info("索引 %s 状态: %s", r["name"], r.get("state"))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
