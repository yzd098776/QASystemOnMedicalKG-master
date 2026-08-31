# coding: utf-8
"""
用药安全核心逻辑（自 app.py /api/drug/* 抽取，阶段三）。

同时被 /api/drug/interaction、/api/drug/contraindication 路由与
问答管线的 drug_safety 意图分支复用，避免重复实现。
行为与原路由完全一致（含别名归一化候选与既有降级逻辑）。
"""

import logging

from core.alias import normalize as normalize_alias

from .graph_db import run_cypher

logger = logging.getLogger(__name__)


def _alias_candidates(value):
    """别名归一化候选序列：归一化名优先，原词兜底（与 app.py 同口径）"""
    if value is None:
        return [None]
    normalized = normalize_alias(value)
    if normalized and normalized != value:
        return [normalized, value]
    return [value]


async def run_drug_interaction(drugs):
    """药物相互作用核心：两两组合查询经疾病建立的间接关系。

    入参为药品名列表（≥2，校验由调用方负责），
    返回 {"interactions": [...]}，结构与原 /api/drug/interaction 一致。
    """
    interactions = []
    for i in range(len(drugs)):
        for j in range(i + 1, len(drugs)):
            query = """
            MATCH (d1:Drug {name: $drug1})<-[:common_drug]-(dis:Disease)-[:common_drug]->(d2:Drug {name: $drug2})
            RETURN dis.name AS disease
            LIMIT 5
            """
            try:
                results = await run_cypher(query, {"drug1": drugs[i], "drug2": drugs[j]})
                if results:
                    diseases = [r["disease"] for r in results]
                    interactions.append({
                        "drug1": drugs[i],
                        "drug2": drugs[j],
                        "risk": "中",
                        "description": f"两种药品均可用于治疗{'、'.join(diseases)}，同时使用前请咨询医生。",
                    })
                else:
                    interactions.append({
                        "drug1": drugs[i],
                        "drug2": drugs[j],
                        "risk": "低",
                        "description": "未发现已知的药物相互作用，但建议遵医嘱使用。",
                    })
            except Exception:
                interactions.append({
                    "drug1": drugs[i],
                    "drug2": drugs[j],
                    "risk": "未知",
                    "description": "暂无相关数据。",
                })
    return {"interactions": interactions}


async def run_drug_contraindication(safe_drug):
    """药品禁忌核心：别名归一化（规范名优先、原词兜底，每候选先精确后模糊）
    定位药品后，合并查询主治疾病、忌吃食物、生产厂商。

    返回与 /api/drug/contraindication 相同的 info 字典；未找到药品返回 None
    （由调用方决定 404 契约）。
    """
    query = """
    MATCH (dr:Drug {name: $name})
    RETURN dr.name AS name
    """
    result = []
    try:
        for candidate in _alias_candidates(safe_drug):
            result = await run_cypher(query, {"name": candidate})
            if not result:
                # 尝试模糊搜索（保持既有降级逻辑）
                fuzzy_q = """
                MATCH (dr:Drug)
                WHERE dr.name CONTAINS $name
                RETURN dr.name AS name
                LIMIT 1
                """
                try:
                    result = await run_cypher(fuzzy_q, {"name": candidate})
                except Exception:
                    result = []
            if result:
                break
    except Exception as e:
        logger.error(f"药品查询失败: {e}")
        return None

    if not result:
        return None

    drug_name = result[0]["name"]

    # 合并查询：主治疾病、忌吃食物、生产厂商
    combined_q = """
    MATCH (dr:Drug {name: $name})
    OPTIONAL MATCH (dr)<-[:common_drug]-(d:Disease)
    OPTIONAL MATCH (d)-[:no_eat]->(f:Food)
    OPTIONAL MATCH (p:Producer)-[:drugs_of]->(dr)
    RETURN collect(DISTINCT d.name) AS diseases,
      collect(DISTINCT f.name) AS foods,
      collect(DISTINCT p.name) AS producers
    """
    try:
        cr = await run_cypher(combined_q, {"name": drug_name})
        if cr:
            r = cr[0]
            info = {
                "name": drug_name,
                "disease": "、".join(r["diseases"][:5]) if r["diseases"] else "暂无",
                "noEat": (r["foods"] or [])[:10],
                "producer": "、".join(r["producers"][:3]) if r["producers"] else "暂无",
                "contra": [],
            }
        else:
            info = {"name": drug_name, "disease": "暂无", "noEat": [], "producer": "暂无", "contra": []}
    except Exception as e:
        logger.error(f"药品详情查询失败: {e}")
        info = {"name": drug_name, "disease": "暂无", "noEat": [], "producer": "暂无", "contra": []}

    return info
