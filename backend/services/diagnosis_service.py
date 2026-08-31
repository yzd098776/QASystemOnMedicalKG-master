# coding: utf-8
"""
加权诊断核心逻辑（自 app.py /api/diagnosis 抽取，阶段三）。

同时被 /api/diagnosis 路由与问答管线的 diagnosis 意图分支复用，
避免重复实现。行为与原路由完全一致：

打分公式：score = Σ(命中症状的 IDF 权重) + log(1 + 疾病先验 get_prob)；
- 症状 IDF 语义：越常见的症状区分度越低、权重越低（由迁移脚本写入）；
- 先验采用 log(1+x) 阻尼叠加，避免离群先验淹没症状证据；
- 先验缺失时用全库疾病先验中位数替代；
- probability 为相对置信度（最高分疾病归一化为 100）。
"""

import logging
import math
import time

from core.alias import normalize as normalize_alias

from .graph_db import run_cypher

logger = logging.getLogger(__name__)

# ========== 诊断先验上下文缓存（等价于原 app.py 的 _diagnosis_prior_context） ==========
_prior_cache = {"value": None, "ts": 0.0}


async def _diagnosis_prior_context():
    """诊断加权上下文（缓存5分钟）：全库疾病先验中位数 + 默认 IDF。
    - 先验中位数：疾病 get_prob 缺失时的替代值（不伪造个体风险，避免一律归零）；
    - 默认 IDF：症状缺失 idf（迁移未执行）时的兜底权重，取总疾病数对应的最大 IDF"""
    if _prior_cache["value"] and time.time() - _prior_cache["ts"] < 300:
        return _prior_cache["value"]
    priors = []
    try:
        rows = await run_cypher(
            "MATCH (d:Disease) WHERE d.get_prob IS NOT NULL RETURN d.get_prob AS p"
        )
        priors = sorted(float(r["p"]) for r in rows if r["p"] is not None)
    except Exception as e:
        logger.error(f"诊断先验中位数查询失败: {e}")
    prior_median = priors[len(priors) // 2] if priors else 0.0
    total_diseases = 0
    try:
        rows = await run_cypher("MATCH (d:Disease) RETURN count(d) AS c")
        total_diseases = rows[0]["c"] if rows else 0
    except Exception:
        total_diseases = 0
    ctx = {
        "prior_median": prior_median,
        "default_idf": math.log(1.0 + max(total_diseases, 1)),
    }
    # 仅当查询真实成功（库中存在疾病节点）时才写缓存；
    # 查询失败返回的降级值不缓存，避免瞬时故障的降级结果被复用
    if total_diseases > 0:
        _prior_cache["value"] = ctx
        _prior_cache["ts"] = time.time()
    return ctx


def _group_evidence_by_input(evidence, symptoms):
    """证据按输入症状归组（阶段三修复，口径与原路由一致）：
    每条输入症状至多贡献一条证据，保证 matchedCount ≤ 输入症状数；
    归一化名命中的证据并入对应输入词条目并在括号内注明库中实际命中名。
    """
    ev_map = {e["symptom"]: e for e in evidence}
    used = set()
    grouped = []
    for s in symptoms:
        for term in (normalize_alias(s), s):
            hit = ev_map.get(term)
            if hit is not None and term not in used:
                used.add(term)
                display = s if term == s else f"{s}（库中命中：{term}）"
                grouped.append({"symptom": display, "weight": hit["weight"]})
                break
    return grouped


async def run_diagnosis(symptoms):
    """加权诊断核心：入参为症状词列表（原词即可，内部做别名扩展），
    返回 {"results": [...]}，结构与原 /api/diagnosis 响应一致。"""
    ctx = await _diagnosis_prior_context()
    # 聚合下推：加权聚合与排序在 Cypher 内完成并按基础分预筛前 100，
    # Python 侧只对预筛结果叠加先验精排取前 20
    query = """
    MATCH (d:Disease)-[:has_symptom]->(s:Symptom)
    WHERE s.name IN $symptoms
    WITH d, collect(DISTINCT {symptom: s.name, weight: coalesce(s.idf, $defaultIdf)}) AS evidence
    WITH d, evidence, reduce(acc = 0.0, e IN evidence | acc + e.weight) AS base
    RETURN d.name AS name, d.desc AS desc, d.cure_department AS department,
           evidence, d.get_prob AS prior
    ORDER BY base DESC
    LIMIT 100
    """
    # 把每个症状的归一化名与原词都纳入匹配集合（去重保序），提升口语化症状词召回
    symptom_terms = []
    for s in symptoms:
        for term in (normalize_alias(s), s):
            if term and term not in symptom_terms:
                symptom_terms.append(term)
    try:
        results = await run_cypher(query, {"symptoms": symptom_terms, "defaultIdf": ctx["default_idf"]})
    except Exception as e:
        logger.error(f"诊断查询失败: {e}")
        return {"results": []}

    # 逐疾病打分：基础分 = Σ命中症状 IDF 权重，再叠加阻尼后的疾病先验；
    # 先验缺失/非数值时用中位数替代；先验贡献 = log(1 + max(prior, 0))
    scored = []
    for r in results:
        evidence = _group_evidence_by_input(r["evidence"] or [], symptoms)
        base_score = sum(float(e["weight"]) for e in evidence)
        prior_value = r["prior"]
        if isinstance(prior_value, (int, float)):
            prior_used = float(prior_value)
        else:
            prior_used = ctx["prior_median"]
        prior_boost = math.log(1.0 + max(prior_used, 0.0))
        score = base_score + prior_boost
        scored.append({
            "name": r["name"],
            "desc": r["desc"] or "暂无简介",
            "department": r["department"] or "暂无",
            "evidence": evidence,
            "prior_used": prior_used,
            "prior_boost": prior_boost,
            "prior": prior_value if isinstance(prior_value, (int, float)) else None,
            "score": score,
        })

    # 按分数降序取前 20（与原接口 LIMIT 20 的数量上限一致）
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:20]

    # 一次性批量查询头部疾病的检查项目（保持原 checks 字段，最多5项）
    checks_map = {}
    if top:
        check_query = """
        MATCH (d:Disease)-[:need_check]->(c:Check)
        WHERE d.name IN $names
        RETURN d.name AS name, collect(c.name) AS checks
        """
        try:
            check_rows = await run_cypher(check_query, {"names": [t["name"] for t in top]})
            for row in check_rows:
                checks_map.setdefault(row["name"], []).extend(row["checks"] or [])
        except Exception as e:
            logger.error(f"诊断检查查询失败: {e}")

    max_score = top[0]["score"] if top else 0.0
    diagnosis_results = []
    for t in top:
        probability = round(t["score"] / max_score * 100) if max_score > 0 else 0
        diagnosis_results.append({
            "name": t["name"],
            "desc": t["desc"],
            "matchedSymptoms": [e["symptom"] for e in t["evidence"]],
            "matchedCount": len(t["evidence"]),
            "probability": probability,
            "department": t["department"],
            "checks": (checks_map.get(t["name"]) or [])[:5],
            "prior": t["prior"],
            "match_evidence": [
                {
                    "symptom": e["symptom"],
                    "weight": round(float(e["weight"]), 4),
                    "contribution": round(float(e["weight"]), 4),
                }
                for e in t["evidence"]
            ],
        })

    return {"results": diagnosis_results}
