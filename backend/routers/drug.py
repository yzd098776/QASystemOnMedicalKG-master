# coding: utf-8
"""用药/食疗路由（阶段五物理拆分）：药品禁忌、相互作用、食物宜忌。"""
import logging

from fastapi import APIRouter, HTTPException

from services.graph_db import run_cypher
from services.drug_service import run_drug_contraindication, run_drug_interaction
from deps import _validate_entity_input, _alias_candidates
from schemas import DrugInteractionRequest

router = APIRouter()
logger = logging.getLogger("app.drug")


# ========== 用药安全接口 ==========
@router.get("/api/drug/contraindication")
async def drug_contraindication(drug: str):
    # 空药品名按未命中处理返回 404（与既有行为一致）；超长返回 400；
    # 查询核心（阶段三）抽取至 services/drug_service，含别名归一化候选与降级逻辑；
    # 服务返回 None 表示未找到药品，由本路由维持既有 404 契约；查询内部异常时
    # 服务同样返回 None（与原实现抛出 500 的分支对应，此处保持 404 更贴近用户语义）
    safe_drug = _validate_entity_input(drug, "药品名称")
    if not safe_drug:
        raise HTTPException(status_code=404, detail="未找到该药品")
    info = await run_drug_contraindication(safe_drug)
    if info is None:
        raise HTTPException(status_code=404, detail="未找到该药品")
    return info


@router.get("/api/food/contraindication")
async def food_contraindication(query: str, type: str = "food"):
    # 空输入按既有契约返回空结构；超长返回 400；查询词经参数化（$name）传入
    safe_query = _validate_entity_input(query, "查询内容")
    if not safe_query:
        if type == "food":
            return {"name": query or "", "diseases": []}
        return {"name": query or "", "doEat": [], "noEat": [], "recommandEat": []}

    if type == "food":
        # 按食物查询（别名归一化：规范名优先、原词兜底）
        q = """
        MATCH (f:Food {name: $name})
        RETURN f.name AS name
        """
        result = []
        effective_query = safe_query
        for candidate in _alias_candidates(safe_query):
            try:
                result = await run_cypher(q, {"name": candidate})
            except Exception:
                result = []
            if result:
                effective_query = candidate
                break

        if not result:
            return {"name": safe_query, "diseases": []}

        # 查询不宜食用的疾病（基于实际命中的食物名）
        disease_q = """
        MATCH (f:Food {name: $name})<-[:no_eat]-(d:Disease)
        RETURN collect(d.name) AS diseases
        """
        try:
            dr = await run_cypher(disease_q, {"name": effective_query})
            diseases = dr[0]["diseases"] if dr else []
        except Exception:
            diseases = []

        return {"name": effective_query, "diseases": diseases}

    else:
        # 按疾病查询（别名归一化：规范名优先、原词兜底）
        q = """
        MATCH (d:Disease {name: $name})
        RETURN d.name AS name
        """
        result = []
        effective_query = safe_query
        for candidate in _alias_candidates(safe_query):
            try:
                result = await run_cypher(q, {"name": candidate})
            except Exception:
                result = []
            if result:
                effective_query = candidate
                break

        if not result:
            return {"name": safe_query, "doEat": [], "noEat": [], "recommandEat": []}

        info = {"name": effective_query}

        # 合并查询：宜吃、忌吃、推荐食物（基于实际命中的疾病名）
        combined_q = """
        MATCH (d:Disease {name: $name})
        OPTIONAL MATCH (d)-[:do_eat]->(f1:Food)
        OPTIONAL MATCH (d)-[:no_eat]->(f2:Food)
        OPTIONAL MATCH (d)-[:recommand_eat]->(f3:Food)
        RETURN collect(DISTINCT f1.name) AS doEat,
          collect(DISTINCT f2.name) AS noEat,
          collect(DISTINCT f3.name) AS recommandEat
        """
        try:
            cr = await run_cypher(combined_q, {"name": effective_query})
            if cr:
                r = cr[0]
                info["doEat"] = (r["doEat"] or [])[:20]
                info["noEat"] = (r["noEat"] or [])[:20]
                info["recommandEat"] = (r["recommandEat"] or [])[:20]
            else:
                info["doEat"] = []
                info["noEat"] = []
                info["recommandEat"] = []
        except Exception:
            info["doEat"] = []
            info["noEat"] = []
            info["recommandEat"] = []

        return info


@router.post("/api/drug/interaction")
async def drug_interaction(req: DrugInteractionRequest):
    # 空药品名跳过（保持既有行为）；超过200字符返回 400；药品名经参数化传入；
    # 相互作用核心逻辑（阶段三）抽取至 services/drug_service，
    # 供本路由与问答管线的 drug_safety 意图分支复用，响应契约不变
    drugs = []
    for d in req.drugs:
        item = _validate_entity_input(d, "药品名称")
        if item:
            drugs.append(item)
    if len(drugs) < 2:
        raise HTTPException(status_code=400, detail="请至少提供两种药品")
    return await run_drug_interaction(drugs)
