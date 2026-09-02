# coding: utf-8
"""问答与百科路由（阶段五物理拆分）：GraphRAG /api/chat、聊天历史 CRUD、每日_tip。"""
import time
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.config import RATE_LIMIT_CHAT_PER_MINUTE
from core.ratelimit import check_rate_limit
from store import (
    profiles_db, chat_history_db, save_json_async, _decrypted_profile, CHAT_HISTORY_FILE,
)
from deps import get_current_user, optional_user
from schemas import ChatRequest, SaveChatRequest
from services.graph_db import run_cypher
from services.rag_pipeline import run_graphrag_chat

router = APIRouter()
logger = logging.getLogger("app.chat")


# ========== 知识百科接口 ==========
@router.get("/api/wiki/daily-tip")
async def daily_tip():
    # 每日随机推荐
    query = """
    MATCH (d:Disease)
    WITH d, rand() AS r
    ORDER BY r
    LIMIT 1
    RETURN d.name AS name, d.desc AS desc, d.prevent AS prevent
    """
    try:
        results = await run_cypher(query)
        if results:
            r = results[0]
            return {
                "title": r["name"],
                "content": f"{r['desc'] or '暂无简介'}。预防措施：{r['prevent'] or '暂无'}",
                "category": "Disease",
            }
    except Exception:
        pass

    return {
        "title": "感冒的预防与治疗",
        "content": "感冒是最常见的呼吸道疾病，由病毒引起。预防措施包括：勤洗手、避免接触患者、增强免疫力。治疗以对症治疗为主，注意休息和多饮水。",
        "category": "Disease",
    }


# ========== DeepSeek AI 问答接口（阶段三：GraphRAG 管线） ==========
# 通用系统提示词（免责声明要求保留）：rag 分支在管线内会替换为
# 带图谱三元组上下文的 GraphRAG 提示词（见 services/rag_pipeline）
SYSTEM_PROMPT = """你是一个专业的医疗健康助手，必须优先使用提供的医疗知识图谱数据回答用户问题。
回答要求：
1. 所有涉及疾病、药品、症状的信息必须来自知识图谱，不确定的内容明确说明
2. 语言通俗易懂，避免专业术语堆砌
3. 禁止给出具体用药剂量和手术方案，只提供一般性建议
4. 所有回答末尾必须添加："以上内容仅供参考，如有不适请及时就医"
5. 结合用户提供的健康档案信息给出个性化建议
"""


@router.post("/api/chat")
async def chat(req: ChatRequest, request: Request, username: str = Depends(optional_user)):
    """智能问答入口（请求契约不变）：仅做限流/参数校验与消息组装，
    急症检测、意图路由、检索增强、DeepSeek 调用与来源溯源均由
    services/rag_pipeline 的 GraphRAG 管线完成（SSE 帧协议见该模块头部说明）"""
    # 限流：IP + 登录用户（可选）双维度，防止滥用大模型接口（超限抛 429）
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit("chat-ip", client_ip, RATE_LIMIT_CHAT_PER_MINUTE)
    if username:
        check_rate_limit("chat-user", username, RATE_LIMIT_CHAT_PER_MINUTE)

    if not req.messages:
        raise HTTPException(status_code=400, detail="消息列表不能为空")
    question = req.messages[-1].content or ""

    # 组装消息列表（[0] 为通用系统提示词，rag 分支在管线内替换为带图谱上下文的提示词）
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 添加健康档案上下文（敏感字段解密后使用）
    if username and username in profiles_db:
        profile = _decrypted_profile(username)
        parts = []
        if profile.get("age"):
            parts.append(f"{profile['age']}岁")
        if profile.get("gender"):
            parts.append(profile["gender"])
        if profile.get("allergy_drug"):
            parts.append(f"药品过敏：{profile['allergy_drug']}")
        if profile.get("medical_history"):
            parts.append(f"病史：{profile['medical_history']}")
        if profile.get("family_history"):
            parts.append(f"家族史：{profile['family_history']}")
        if parts:
            messages.append({
                "role": "system",
                "content": f"用户健康档案：{'，'.join(parts)}",
            })

    # 添加用户上下文与对话历史（与既有契约一致，最多取最近 10 条）
    if req.context:
        messages.append({"role": "system", "content": req.context})
    for msg in req.messages[-10:]:
        messages.append({"role": msg.role, "content": msg.content})

    return StreamingResponse(
        run_graphrag_chat(messages, question),
        media_type="text/event-stream",
    )


# ========== 聊天记录存储 ==========

# （聊天历史模型已抽至 schemas.py）

@router.get("/api/chat/history")
async def get_chat_history(username: str = Depends(get_current_user)):
    """获取当前用户的所有聊天记录"""
    user_history = chat_history_db.get(username, {"sessions": []})
    return user_history


@router.post("/api/chat/history/save")
async def save_chat_history(req: SaveChatRequest, username: str = Depends(get_current_user)):
    """保存/更新一个对话会话"""
    if username not in chat_history_db:
        chat_history_db[username] = {"sessions": []}

    sessions = chat_history_db[username]["sessions"]
    # 查找是否已有该 session
    for i, s in enumerate(sessions):
        if s["id"] == req.session_id:
            sessions[i] = {
                "id": req.session_id,
                "name": req.session_name,
                "messages": [m.model_dump() for m in req.messages],
            }
            await save_json_async(CHAT_HISTORY_FILE, chat_history_db)
            return {"ok": True}

    # 新会话，追加到头部
    sessions.insert(0, {
        "id": req.session_id,
        "name": req.session_name,
        "messages": [m.model_dump() for m in req.messages],
    })
    await save_json_async(CHAT_HISTORY_FILE, chat_history_db)
    return {"ok": True}


@router.delete("/api/chat/history")
async def clear_chat_history(username: str = Depends(get_current_user)):
    """一键清除当前用户所有聊天记录"""
    chat_history_db[username] = {"sessions": []}
    await save_json_async(CHAT_HISTORY_FILE, chat_history_db)
    return {"ok": True}


@router.delete("/api/chat/history/{session_id}")
async def delete_chat_session(session_id: str, username: str = Depends(get_current_user)):
    """删除单个会话并持久化（修复：此前前端仅本地移除、后端无单删接口，
    重启后已删会话会从存储中复活）。会话不存在同样返回 ok，保持幂等。"""
    history = chat_history_db.get(username)
    if history:
        sessions = history.get("sessions", [])
        kept = [s for s in sessions if s.get("id") != session_id]
        if len(kept) != len(sessions):
            history["sessions"] = kept
            await save_json_async(CHAT_HISTORY_FILE, chat_history_db)
    return {"ok": True}
