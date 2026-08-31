# coding: utf-8
"""
图谱检索器（阶段三 3.2 + 3.3）。

职责：
1. 混合锚点召回：关键词路（别名归一化 + CONTAINS，走阶段二标签索引）
   与向量路（vector_index.vector_recall，余弦相似）双路加权融合，
   权重由 .env 的 HYBRID_KEYWORD_WEIGHT / HYBRID_VECTOR_WEIGHT 控制；
2. 三元组抽取：对每个锚点实体取 1 跳邻居三元组（带标签定位，
   复用阶段二索引），每实体上限 ~20 条、总上限 ~60 条，
   每条三元组分配稳定 id（T1..Tn）用于来源标注。
"""

import logging

from core.config import HYBRID_KEYWORD_WEIGHT, HYBRID_VECTOR_WEIGHT
from core.alias import normalize as normalize_alias

from .graph_db import run_cypher
from .vector_index import vector_recall

logger = logging.getLogger(__name__)

# 七类实体标签（与 app.py ALLOWED_LABELS 一致），用于生成带标签的定位分支
_LABELS = ("Check", "Department", "Disease", "Drug", "Food", "Producer", "Symptom")

# 检索上限（任务约定：每实体 ~20 条三元组，总上限 ~60 条）
TRIPLES_PER_ENTITY = 20
TRIPLES_TOTAL = 60
MAX_ANCHORS = 6


def _locate_union(var: str, param: str) -> str:
    """生成「按 name 跨七类标签查节点」的 CALL 子查询片段（含 CALL 包裹）。

    与 app.py._name_match_union 同一思路：逐标签分支命中阶段二建立的
    标签级约束/索引，避免无标签全表扫描。标签仅来自内置白名单 _LABELS，
    name 值以 $参数传入，无注入风险。
    """
    branches = [
        "MATCH (%s:%s {name: $%s}) RETURN %s" % (var, label, param, var)
        for label in _LABELS
    ]
    return "CALL () {\n" + "\nUNION ALL\n".join(branches) + "\n}"


async def keyword_recall(terms, limit: int = 10):
    """关键词召回：对每个检索词做精确 + CONTAINS 跨标签查询。

    返回 [{"name", "label", "rank"}]，rank 为合并后的出现次序（1 起）。
    """
    hits = []
    seen = set()
    for term in terms:
        if not term:
            continue
        # 精确匹配优先（命中标签级约束索引）
        exact_q = (
            _locate_union("n", "term") + "\n"
            "RETURN n.name AS name, labels(n)[0] AS label LIMIT $limit"
        )
        rows = []
        try:
            rows = await run_cypher(exact_q, {"term": term, "limit": limit})
        except Exception as e:
            logger.warning("关键词精确召回失败（%s）: %s", term, e)
        if not rows:
            # 精确未命中则模糊召回（CONTAINS；保持既有降级风格）
            fuzzy_q = (
                "MATCH (n) WHERE n.name CONTAINS $term "
                "RETURN n.name AS name, labels(n)[0] AS label LIMIT $limit"
            )
            try:
                rows = await run_cypher(fuzzy_q, {"term": term, "limit": limit})
            except Exception as e:
                logger.warning("关键词模糊召回失败（%s）: %s", term, e)
        for r in rows or []:
            if r.get("name") and r["name"] not in seen:
                seen.add(r["name"])
                hits.append({"name": r["name"], "label": r.get("label")})
    for i, h in enumerate(hits):
        h["rank"] = i + 1
    return hits


async def hybrid_anchors(question: str, entities, max_anchors: int = MAX_ANCHORS):
    """双路加权融合产出锚点实体名列表（供三元组抽取）。

    - 关键词路得分 = 1 / rank（越靠前越高），权重 HYBRID_KEYWORD_WEIGHT；
    - 向量路得分 = 余弦相似度（0~1），权重 HYBRID_VECTOR_WEIGHT；
    - 同名实体两路得分叠加，按总分降序取前 max_anchors。
    """
    scores = {}

    # 关键词路：识别实体的规范名 + 原词 + 别名归一化名（去重保序）
    terms = []
    for e in entities or []:
        for term in (e.get("name"), e.get("raw")):
            if term and term not in terms:
                terms.append(term)
        norm = normalize_alias(e.get("raw") or "")
        if norm and norm not in terms:
            terms.append(norm)
    kw_hits = await keyword_recall(terms)
    for h in kw_hits:
        scores[h["name"]] = scores.get(h["name"], 0.0) + HYBRID_KEYWORD_WEIGHT / h["rank"]

    # 向量路：整句嵌入召回（口语表述补位）
    vec_hits = await vector_recall(question, per_label_k=10)
    for h in vec_hits:
        scores[h["name"]] = scores.get(h["name"], 0.0) + HYBRID_VECTOR_WEIGHT * h["score"]

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    anchors = [name for name, _ in ordered[:max_anchors]]
    logger.info(
        "混合检索锚点（关键词权重=%.2f，向量权重=%.2f）: %s",
        HYBRID_KEYWORD_WEIGHT, HYBRID_VECTOR_WEIGHT, anchors,
    )
    return anchors


async def fetch_triples(anchor_names, per_entity: int = TRIPLES_PER_ENTITY, total: int = TRIPLES_TOTAL):
    """对锚点实体抽取 1 跳邻居三元组并分配稳定 id。

    返回 [{"id": "T1", "subject", "predicate", "object",
           "subject_label", "object_label"}]，按 (主,谓,宾) 去重，
    每实体上限 per_entity 条，总量上限 total 条。
    """
    triples = []
    seen = set()
    for name in anchor_names:
        if len(triples) >= total:
            break
        query = (
            _locate_union("n", "name") + "\n"
            "MATCH (a)-[r]->(b) WHERE a = n OR b = n\n"
            "RETURN a.name AS subject, labels(a)[0] AS subject_label,\n"
            "       type(r) AS predicate,\n"
            "       b.name AS object, labels(b)[0] AS object_label\n"
            "LIMIT $limit"
        )
        try:
            rows = await run_cypher(query, {"name": name, "limit": per_entity})
        except Exception as e:
            logger.warning("锚点 %s 三元组抽取失败: %s", name, e)
            continue
        for r in rows or []:
            key = (r.get("subject"), r.get("predicate"), r.get("object"))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            triples.append({
                "id": f"T{len(triples) + 1}",
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "subject_label": r.get("subject_label"),
                "object_label": r.get("object_label"),
            })
            if len(triples) >= total:
                break
    return triples


def triples_to_context(triples) -> str:
    """把三元组列表渲染为注入提示词的结构化上下文（T 编号与 id 一致）"""
    lines = []
    for t in triples:
        lines.append(
            f"{t['id']}: {t['subject']} -[{t['predicate']}]-> {t['object']}"
        )
    return "\n".join(lines)


async def fetch_entity_detail(name: str):
    """单实体百科数据：节点属性 + 1 跳关系（含方向），供结构化回答使用。

    返回 {"name", "label", "props": {...}, "relations": [{predicate, object, direction}]}
    或 None（图谱中不存在该实体）。
    """
    query = (
        _locate_union("n", "name") + "\n"
        "OPTIONAL MATCH (n)-[r_out]->(m_out)\n"
        "OPTIONAL MATCH (m_in)-[r_in]->(n)\n"
        "RETURN labels(n)[0] AS label, properties(n) AS props,\n"
        "  collect(DISTINCT {predicate: type(r_out), object: m_out.name, direction: 'out'}) AS outs,\n"
        "  collect(DISTINCT {predicate: type(r_in), object: m_in.name, direction: 'in'}) AS ins"
    )
    try:
        rows = await run_cypher(query, {"name": name})
    except Exception as e:
        logger.warning("实体详情查询失败（%s）: %s", name, e)
        return None
    if not rows:
        return None
    r = rows[0]
    props = dict(r.get("props") or {})
    # 剔除向量属性（256 维嵌入），避免其流入结构化回答与日志
    props.pop("embedding", None)
    relations = []
    seen_rel = set()
    for rel in (r.get("outs") or []) + (r.get("ins") or []):
        if not rel.get("object") or not rel.get("predicate"):
            continue
        key = (rel["predicate"], rel["object"], rel["direction"])
        if key in seen_rel:
            continue
        seen_rel.add(key)
        relations.append(rel)
    return {
        "name": name,
        "label": r.get("label") or "Entity",
        "props": props,
        "relations": relations,
    }
