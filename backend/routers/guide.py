# coding: utf-8
"""就医指南路由（阶段五物理拆分）：按疾病/症状荐科室、检查项目详情。"""
import logging

from fastapi import APIRouter, HTTPException

from services.graph_db import run_cypher
from deps import _validate_entity_input, _alias_candidates

router = APIRouter()
logger = logging.getLogger("app.guide")


# ========== 就医指南接口 ==========
@router.get("/api/guide/department")
async def guide_department(query: str):
    # 空输入返回空科室列表，保持既有“未命中返回空结构”行为；超长返回 400；
    # 查询词经参数化（$query）传入，无需黑名单过滤
    safe_query = _validate_entity_input(query, "查询内容")
    if not safe_query:
        return {"departments": []}

    # 合并查询：科室及其关联的疾病和检查
    q = """
    MATCH (d:Disease)-[:belongs_to]->(dep:Department)
    WHERE d.name CONTAINS $query
    WITH DISTINCT dep
    OPTIONAL MATCH (d2:Disease)-[:belongs_to]->(dep)
    OPTIONAL MATCH (d2)-[:need_check]->(c:Check)
    RETURN dep.name AS name,
      collect(DISTINCT d2.name) AS diseases,
      collect(DISTINCT c.name) AS checks
    LIMIT 5
    """
    try:
        # 别名归一化：候选按序尝试（规范名优先、原词兜底）；每个候选先按疾病名匹配，
        # 未命中再按症状名匹配（保持既有两级降级逻辑），命中即返回（最多4次查询）
        results = []
        for candidate in _alias_candidates(safe_query):
            try:
                results = await run_cypher(q, {"query": candidate})
            except Exception:
                results = []
            if not results or not results[0].get("name"):
                # 查症状关联的疾病对应的科室（第二级降级）
                q2 = """
                MATCH (s:Symptom)<-[:has_symptom]-(d:Disease)-[:belongs_to]->(dep:Department)
                WHERE s.name CONTAINS $query
                WITH DISTINCT dep
                OPTIONAL MATCH (d2:Disease)-[:belongs_to]->(dep)
                OPTIONAL MATCH (d2)-[:need_check]->(c:Check)
                RETURN dep.name AS name,
                  collect(DISTINCT d2.name) AS diseases,
                  collect(DISTINCT c.name) AS checks
                LIMIT 5
                """
                try:
                    results = await run_cypher(q2, {"query": candidate})
                except Exception:
                    results = []
            if results and results[0].get("name"):
                break
    except Exception:
        results = []

    departments = []
    for r in results:
        if not r.get("name"):
            continue
        departments.append({
            "name": r["name"],
            "description": f"{r['name']}是医院的重要科室",
            "diseases": (r.get("diseases") or [])[:10],
            "checks": (r.get("checks") or [])[:10],
        })

    return {"departments": departments}


@router.get("/api/guide/check")
async def guide_check(query: str):
    # 空输入按未命中处理返回 404（与既有行为一致）；超长返回 400；
    # 查询词经参数化（$name）传入，无需黑名单过滤
    safe_query = _validate_entity_input(query, "查询内容")
    if not safe_query:
        raise HTTPException(status_code=404, detail="未找到该检查项目")

    q = """
    MATCH (c:Check {name: $name})
    RETURN c.name AS name, properties(c) AS props
    """
    fuzzy_q = """
    MATCH (c:Check)
    WHERE c.name CONTAINS $name
    RETURN c.name AS name, properties(c) AS props
    LIMIT 1
    """
    # 别名归一化：候选按序尝试（规范名优先、原词兜底），每个候选先精确后模糊；
    # 全部未命中保持既有 404 契约（最多4次查询）
    results = []
    for candidate in _alias_candidates(safe_query):
        try:
            results = await run_cypher(q, {"name": candidate})
        except Exception:
            results = []
        if not results:
            # 模糊搜索（保持既有降级逻辑）
            try:
                results = await run_cypher(fuzzy_q, {"name": candidate})
            except Exception:
                results = []
        if results:
            break

    if not results:
        raise HTTPException(status_code=404, detail="未找到该检查项目")

    check_name = results[0]["name"]
    props = results[0]["props"]

    # 获取关联疾病
    disease_q = """
    MATCH (d:Disease)-[:need_check]->(c:Check {name: $name})
    RETURN collect(d.name) AS diseases
    """
    try:
        dr = await run_cypher(disease_q, {"name": check_name})
        related_diseases = dr[0]["diseases"][:10] if dr else []
    except Exception:
        related_diseases = []

    return {
        "name": check_name,
        "purpose": props.get("desc", "暂无"),
        "process": "请咨询医院了解具体检查流程",
        "precautions": "请遵医嘱",
        "normalRange": "请参考医院报告单",
        "relatedDiseases": related_diseases,
    }
