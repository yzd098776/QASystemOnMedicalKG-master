# coding: utf-8
"""疾病自查路由（阶段五物理拆分）：加权诊断，核心逻辑复用 services/diagnosis_service。"""
from fastapi import APIRouter, HTTPException

from services.diagnosis_service import run_diagnosis
from deps import _validate_entity_input
from schemas import DiagnosisRequest

router = APIRouter()


# ========== 疾病自查接口 ==========
@router.post("/api/diagnosis")
async def diagnosis(req: DiagnosisRequest):
    # 空症状项跳过（保持既有行为）；单个症状项超过200字符返回 400；
    # 症状列表经参数化（$symptoms）传入，无需黑名单过滤；
    # 加权诊断核心逻辑（阶段三）已抽取至 services/diagnosis_service，
    # 供本路由与问答管线的 diagnosis 意图分支复用，行为与口径不变：
    # score = Σ(命中症状 IDF 权重) + log(1 + 疾病先验)，按分取前 20，
    # 响应字段与原契约完全一致（新增字段 match_evidence / prior 保留）
    symptoms = []
    for s in req.symptoms:
        item = _validate_entity_input(s, "症状项")
        if item:
            symptoms.append(item)
    if not symptoms:
        raise HTTPException(status_code=400, detail="请至少提供一个症状")
    return await run_diagnosis(symptoms)
