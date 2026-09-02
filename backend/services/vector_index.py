# coding: utf-8
"""
向量检索补位（阶段三 3.3）。

选型结论（详见 README「向量检索方案」）：
- 向量存储：Neo4j 5.26 原生 vector index（免独立向量库运维），
  按标签（Disease/Drug/Symptom）各建一个索引；
- 嵌入模型：纯 Python 哈希字符 n-gram 嵌入（feature hashing，维度由
  EMBEDDING_DIM 控制，默认 256），零新增依赖。

局限性（必须知晓）：
- 哈希 n-gram 嵌入只捕捉「字面重叠」相似度（共享字符越多越相似），
  不具备真正的语义理解能力；对纯口语改写（字面完全不重叠）召回有限；
- 因此检索采用「关键词 + 向量」双路加权融合，向量路仅作补位。

升级路径：未来把 embed() 的实现替换为真实嵌入模型
（如 sentence-transformers 本地模型或厂商 embedding API），
重跑 backend/scripts/build_vector_index.py 重建节点嵌入与索引即可，
检索调用方接口（vector_recall）保持不变。
"""

import hashlib
import logging
import math
import re
import time

from core.config import EMBEDDING_DIM, VECTOR_INDEX_PREFIX

logger = logging.getLogger(__name__)

# 参与向量化的三类实体标签（name + desc 生成嵌入）
VECTOR_LABELS = ("Disease", "Drug", "Symptom")

# 索引名只允许字母数字下划线（防止拼接注入；索引名不可参数化）
_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def index_name_for(label: str) -> str:
    """按标签生成向量索引名，如 kg_embedding_disease"""
    name = f"{VECTOR_INDEX_PREFIX}_{label.lower()}"
    if not _NAME_RE.match(name):
        raise ValueError(f"非法向量索引名: {name}")
    return name


def _char_ngrams(text: str, ns=(1, 2, 3)):
    """提取字符级 n-gram 序列（中文按字符切分，天然适配短文本）"""
    text = text.strip().lower()
    for n in ns:
        if len(text) < n:
            continue
        for i in range(len(text) - n + 1):
            yield text[i:i + n]


def embed_hash(text: str, dim: int = None):
    """纯 Python 哈希字符 n-gram 嵌入（feature hashing）。

    每个 n-gram 经 blake2b 哈希映射到固定维度桶，哈希位决定符号（+1/-1），
    累加后 L2 归一化。零依赖、确定性（同一文本恒得同一向量）。
    局限见模块顶部说明；阶段 B 起作为语义嵌入不可用时的兜底路径，
    dim 可与语义索引维度对齐（保证降级后向量查询仍合法）。
    """
    dim = dim or EMBEDDING_DIM
    vec = [0.0] * dim
    if not isinstance(text, str) or not text.strip():
        return vec
    for gram in _char_ngrams(text):
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        idx = h % dim
        sign = 1.0 if (h >> 63) & 1 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed(text: str):
    """兼容入口：默认维度的哈希嵌入（provider=hash 路径与升级前逐位一致）"""
    return embed_hash(text, dim=EMBEDDING_DIM)


def _active_dim() -> int:
    """当前生效嵌入维度（语义模式取 SEMANTIC_EMBEDDING_DIM）。

    延迟导入规避 vector_index <-> embedding_provider 的模块级循环依赖。
    """
    from .embedding_provider import active_dim
    return active_dim()


def create_index_cypher(label: str) -> str:
    """生成建向量索引的幂等语句（维度取当前生效嵌入维度，与写入嵌入一致）。

    Neo4j 社区版说明：向量索引作用于普通 LIST<FLOAT> 属性（n.embedding），
    无需也不得使用 vector() 类型属性；索引维度创建后不可变更，
    变更必须 DROP 再 CREATE（由 build_vector_index.py 自动检测处理）。
    """
    return (
        f"CREATE VECTOR INDEX {index_name_for(label)} IF NOT EXISTS "
        f"FOR (n:{label}) ON (n.embedding) "
        f"OPTIONS {{indexConfig: {{"
        f"`vector.dimensions`: {_active_dim()}, "
        f"`vector.similarity_function`: 'cosine'"
        f"}}}}"
    )


def ensure_vector_indexes(run_sync):
    """幂等地为三类标签建立向量索引，并等待索引上线。

    run_sync: 同步 Cypher 执行函数（签名 (query, params) -> rows）。
    索引名已在 index_name_for 中做白名单校验，无注入风险。
    """
    for label in VECTOR_LABELS:
        run_sync(create_index_cypher(label), {})
        logger.info("向量索引 %s 创建语句已执行（IF NOT EXISTS 幂等）", index_name_for(label))
    # 等待索引变为 ONLINE（queryNodes 只可用在线索引），最多等待 120 秒
    deadline = time.time() + 120
    pending = set(index_name_for(label) for label in VECTOR_LABELS)
    while pending and time.time() < deadline:
        try:
            rows = run_sync("SHOW INDEXES YIELD name, state RETURN name, state", {})
        except Exception as e:  # noqa: BLE001 启动期索引状态查询失败不致命，等待重试
            logger.warning("查询索引状态失败，稍后重试: %s", e)
            time.sleep(2)
            continue
        online = {r["name"] for r in rows if r.get("state") == "ONLINE"}
        pending -= online
        if pending:
            time.sleep(2)
    if pending:
        logger.warning("以下向量索引未在超时前上线（可稍后重试或检查 Neo4j 日志）: %s", sorted(pending))
    else:
        logger.info("全部向量索引已上线")


_INDEX_DIM_CACHE = {"dim": None, "ts": 0.0}


async def query_index_dim():
    """读取 Neo4j 现存向量索引的实际维度（60s 缓存；索引缺失/查询失败返回 None）。

    用途：语义 provider 降级为哈希向量时按此维度产出兜底向量，
    保证降级路径的查询仍与索引匹配（等价升级前哈希行为，不因维度不匹配空转）。
    """
    import time as _time
    from .graph_db import run_cypher

    now = _time.time()
    if _INDEX_DIM_CACHE["dim"] is not None and now - _INDEX_DIM_CACHE["ts"] < 60:
        return _INDEX_DIM_CACHE["dim"]
    try:
        # 实测 SHOW VECTOR INDEXES 的 options 键形随版本变化，
        # 用通用 SHOW INDEXES（build 脚本维度检测同源，可靠）
        rows = await run_cypher(
            "SHOW INDEXES YIELD name, type, options RETURN name, type, options", {})
    except Exception:  # noqa: BLE001 老版本语法/权限问题：拿不到就按 None 处理
        return None
    for r in rows:
        if r.get("name") in {index_name_for(l) for l in VECTOR_LABELS}:
            cfg = (r.get("options") or {}).get("indexConfig") or {}
            dim = cfg.get("`vector.dimensions`") or cfg.get("vector.dimensions")
            if dim:
                _INDEX_DIM_CACHE.update(dim=int(dim), ts=now)
                return int(dim)
    return None


async def vector_recall(question: str, per_label_k: int = 10):
    """向量召回：对三类标签的向量索引分别执行 queryNodes，合并返回。

    返回 [{"name": 实体名, "label": 标签, "score": 相似度分}]，按分数降序。
    索引不可用/查询失败时返回空列表（由调用方降级为纯关键词检索）。
    注意：db.index.vector.queryNodes 的索引名必须为字面量（不可参数化），
    索引名来自本模块白名单校验的生成函数，不接受外部输入。

    查询嵌入经 embedding_provider 统一出口：provider=hash 时与升级前逐位一致；
    语义模式下远程失败/超时/熔断由 provider 内部降级为哈希向量（维度对齐索引），
    本函数无须再关心降级细节。
    """
    from .graph_db import run_cypher
    from .embedding_provider import embed_query_async

    # 降级哈希向量对齐索引实际维度（provider=hash 时维度天然一致，无影响）
    fallback_dim = await query_index_dim()
    query_vec = await embed_query_async(question, fallback_dim=fallback_dim)
    if not any(query_vec):
        return []
    hits = []
    for label in VECTOR_LABELS:
        idx_name = index_name_for(label)
        cypher = (
            f"CALL db.index.vector.queryNodes('{idx_name}', $k, $query) "
            f"YIELD node, score "
            f"RETURN node.name AS name, '{label}' AS label, score "
            f"ORDER BY score DESC"
        )
        try:
            rows = await run_cypher(cypher, {"k": per_label_k, "query": query_vec})
            for r in rows:
                if r.get("name"):
                    hits.append({"name": r["name"], "label": label, "score": float(r.get("score") or 0.0)})
        except Exception as e:
            # 索引缺失/未上线（如未跑填充脚本）时静默降级，不影响关键词路
            logger.warning("向量索引 %s 查询失败（该路召回降级为空）: %s", idx_name, e)
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits
