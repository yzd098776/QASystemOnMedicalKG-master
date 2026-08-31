# coding: utf-8
"""健康管理路由（阶段五物理拆分）：预防/慢病计划生成（DeepSeek）+ 记录/计划 CRUD。"""
import json
import time
import logging
import traceback
from datetime import datetime, timezone

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request

from core.config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL
from store import (
    health_records_db, health_plans_db, save_json_async, _decrypted_profile,
    HEALTH_RECORDS_FILE, HEALTH_PLANS_FILE,
)
from deps import get_current_user
from schemas import HealthRecord

router = APIRouter()
logger = logging.getLogger("app.health")


# ========== 健康管理接口 ==========
@router.post("/api/health/prevention")
async def health_prevention(request: Request, username: str = Depends(get_current_user)):
    # 档案敏感字段解密后读取；请求体中自带的 profile 为前端传入的明文，直接使用
    profile = _decrypted_profile(username)
    body = await request.json()
    profile = body.get("profile") or profile

    age = profile.get("age", "未知")
    gender = profile.get("gender", "未知")
    family_history = profile.get("family_history", "无")
    medical_history = profile.get("medical_history", "无")
    allergy_drug = profile.get("allergy_drug", "无")

    prompt = f"""你是一位专业的健康管理医生。请根据以下用户健康档案，生成个性化的疾病预防计划。

用户档案：
- 年龄：{age}岁
- 性别：{gender}
- 家族病史：{family_history}
- 既往病史：{medical_history}
- 药品过敏：{allergy_drug}

请以 JSON 格式返回，结构如下（不要包含 markdown 代码块标记）：
{{
  "items": [
    {{
      "disease": "疾病名称",
      "reason": "为什么该用户需要预防此疾病（结合档案说明）",
      "measures": ["预防措施1", "预防措施2", "预防措施3", "预防措施4"]
    }}
  ],
  "dailyTips": {{
    "diet": "针对该用户的饮食建议",
    "exercise": "针对该用户的运动建议",
    "rest": "针对该用户的作息建议"
  }}
}}

要求：
1. 根据用户年龄、性别、家族病史等个性化推荐 3-5 种需重点预防的疾病
2. 每个疾病的预防措施要具体、可操作
3. 日常建议要符合用户个人情况"""

    if DEEPSEEK_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(
                    DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": "你是专业的医疗健康助手，返回纯 JSON，不要 markdown 代码块。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 2000,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    # 清理可能的 markdown 代码块
                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                    if content.startswith("json"):
                        content = content[4:].strip()
                    result = json.loads(content)
                    return result
                else:
                    logger.warning(f"DeepSeek API 返回 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"AI 生成预防计划失败: {e}\n{traceback.format_exc()}")

    # 降级方案：基于档案硬编码
    items = []
    if "糖尿病" in family_history:
        items.append({"disease": "糖尿病", "reason": "家族中有糖尿病病史，患病风险较高", "measures": ["控制饮食，减少糖分摄入", "定期检测血糖", "保持适量运动", "控制体重"]})
    if "高血压" in family_history:
        items.append({"disease": "高血压", "reason": "家族中有高血压病史", "measures": ["减少盐分摄入", "保持规律作息", "适度运动", "定期测量血压"]})
    if age and str(age).isdigit() and int(age) > 40:
        items.append({"disease": "心脑血管疾病", "reason": f"{age}岁属于高发年龄段", "measures": ["定期体检", "控制三高", "戒烟限酒", "保持良好心态"]})
    items.append({"disease": "感冒", "reason": "最常见的呼吸道疾病", "measures": ["勤洗手", "避免接触患者", "增强免疫力", "注意保暖"]})
    return {"items": items, "dailyTips": {"diet": "均衡饮食，多吃蔬菜水果", "exercise": "每周至少150分钟中等强度运动", "rest": "保证7-8小时睡眠"}}


@router.post("/api/health/chronic")
async def health_chronic(request: Request, username: str = Depends(get_current_user)):
    body = await request.json()
    disease = body.get("disease", "")
    # 档案敏感字段解密后读取；请求体中自带的 profile 为前端传入的明文，直接使用
    profile = body.get("profile") or _decrypted_profile(username)

    age = profile.get("age", "未知")
    gender = profile.get("gender", "未知")
    medical_history = profile.get("medical_history", "无")
    allergy_drug = profile.get("allergy_drug", "无")

    prompt = f"""你是一位专业的慢性病管理医生。请为用户生成「{disease}」的个性化管理计划。

用户档案：
- 年龄：{age}岁
- 性别：{gender}
- 既往病史：{medical_history}
- 药品过敏：{allergy_drug}

请以 JSON 格式返回（不要包含 markdown 代码块标记）：
{{
  "name": "{disease}管理计划",
  "goal": "具体的管理目标",
  "diet": ["饮食建议1", "饮食建议2", "饮食建议3", "饮食建议4"],
  "exercise": ["运动建议1", "运动建议2", "运动建议3"],
  "checks": ["检查项目1", "检查项目2", "检查项目3"],
  "medicationReminder": "用药提醒（结合用户过敏史）"
}}

要求：
1. 饮食、运动、检查建议要具体、可执行
2. 用药提醒要考虑用户的药品过敏情况
3. 管理目标要量化"""

    if DEEPSEEK_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(
                    DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": "你是专业的医疗健康助手，返回纯 JSON，不要 markdown 代码块。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 2000,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                    if content.startswith("json"):
                        content = content[4:].strip()
                    result = json.loads(content)
                    return result
                else:
                    logger.warning(f"DeepSeek API 返回 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"AI 生成慢性病管理计划失败: {e}\n{traceback.format_exc()}")

    # 降级方案
    plans = {
        "高血压": {"name": "高血压管理计划", "goal": "将血压控制在140/90mmHg以下", "diet": ["低盐饮食，每日盐摄入<6g", "多吃富含钾的食物", "限制饮酒", "减少高脂肪食物"], "exercise": ["每天步行30分钟", "太极拳或瑜伽", "避免剧烈运动"], "checks": ["每周测量血压", "每月血脂检查", "每季度肾功能检查"], "medicationReminder": "请按时服用降压药，不要擅自停药"},
        "糖尿病": {"name": "糖尿病管理计划", "goal": "空腹血糖<7.0mmol/L，糖化血红蛋白<7%", "diet": ["控制总热量摄入", "少食多餐", "选择低GI食物", "增加膳食纤维"], "exercise": ["餐后30分钟开始运动", "每天步行或慢跑30分钟", "适当力量训练"], "checks": ["每日监测血糖", "每3个月检查糖化血红蛋白", "每年眼底检查"], "medicationReminder": "请按时服用降糖药，注意低血糖症状"},
    }
    if disease in plans:
        return plans[disease]
    return {"name": f"{disease}管理计划", "goal": "控制病情，提高生活质量", "diet": ["均衡饮食", "避免刺激性食物", "适量饮水"], "exercise": ["适度运动", "循序渐进", "避免过度劳累"], "checks": ["定期复查", "遵医嘱检查"], "medicationReminder": "请遵医嘱按时服药"}


@router.get("/api/health/records")
async def get_health_records(username: str = Depends(get_current_user)):
    records = health_records_db.get(username, [])
    # 为旧记录补上 _id
    changed = False
    for i, r in enumerate(records):
        if "_id" not in r:
            r["_id"] = f"{r.get('date', 'unknown')}_{i}_{int(time.time() * 1000)}"
            changed = True
    if changed:
        await save_json_async(HEALTH_RECORDS_FILE, health_records_db)
    return {"records": records}


@router.post("/api/health/records")
async def save_health_record(record: HealthRecord, username: str = Depends(get_current_user)):
    if username not in health_records_db:
        health_records_db[username] = []
    rec = record.model_dump()
    rec["_id"] = f"{rec['date']}_{int(time.time() * 1000)}"
    health_records_db[username].append(rec)
    # 按日期排序
    health_records_db[username].sort(key=lambda x: x["date"], reverse=True)
    await save_json_async(HEALTH_RECORDS_FILE, health_records_db)
    return {"message": "记录已保存"}


@router.delete("/api/health/records/{record_id}")
async def delete_health_record(record_id: str, username: str = Depends(get_current_user)):
    """删除指定健康记录"""
    if username not in health_records_db:
        raise HTTPException(status_code=404, detail="无记录")
    records = health_records_db[username]
    new_records = [r for r in records if r.get("_id") != record_id and r.get("date") != record_id]
    if len(new_records) == len(records):
        raise HTTPException(status_code=404, detail="未找到该记录")
    health_records_db[username] = new_records
    await save_json_async(HEALTH_RECORDS_FILE, health_records_db)
    return {"message": "记录已删除"}


# ========== 健康计划存储接口 ==========
@router.get("/api/health/plans")
async def get_health_plans(username: str = Depends(get_current_user)):
    plans = health_plans_db.get(username, [])
    return {"plans": plans}


@router.post("/api/health/plans")
async def save_health_plan(request: Request, username: str = Depends(get_current_user)):
    body = await request.json()
    plan_type = body.get("type", "prevention")
    data = body.get("data", {})
    disease = body.get("disease", "")

    plan = {
        "_id": f"{plan_type}_{int(time.time() * 1000)}",
        "type": plan_type,
        "disease": disease,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    if username not in health_plans_db:
        health_plans_db[username] = []
    health_plans_db[username].insert(0, plan)
    await save_json_async(HEALTH_PLANS_FILE, health_plans_db)
    return {"message": "计划已保存", "plan": plan}


@router.delete("/api/health/plans")
async def clear_health_plans(username: str = Depends(get_current_user)):
    health_plans_db[username] = []
    await save_json_async(HEALTH_PLANS_FILE, health_plans_db)
    return {"message": "所有计划已清空"}
