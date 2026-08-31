# coding: utf-8
"""
请求体模型层（阶段五分层重构）：集中 app.py 原内联的全部 Pydantic 模型，
行为（字段、校验约束、默认值）逐字不变。

注：/api/health/prevention、/api/health/chronic、/api/health/plans(POST) 三个
LLM 代理类接口沿用 `await request.json()` 裸 dict 读取（缺字段走 .get 默认），
为保证其宽松输入契约不漂移（贸然引入强校验模型会把原本容错的请求变成 422），
本阶段有意保留原样，不纳入 schemas。
"""

from typing import Optional, List

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    email: str
    password: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ProfileUpdate(BaseModel):
    age: Optional[int] = None
    gender: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    blood_type: Optional[str] = None
    allergy_drug: Optional[str] = None
    allergy_food: Optional[str] = None
    medical_history: Optional[str] = None
    family_history: Optional[str] = None
    smoking: Optional[bool] = False
    drinking: Optional[bool] = False


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    context: Optional[str] = None


class DiagnosisRequest(BaseModel):
    symptoms: List[str]


class DrugInteractionRequest(BaseModel):
    drugs: List[str]


class HealthRecord(BaseModel):
    date: str
    weight: Optional[float] = None
    bloodPressureHigh: Optional[int] = None
    bloodPressureLow: Optional[int] = None
    bloodSugar: Optional[float] = None
    heartRate: Optional[int] = None
    note: Optional[str] = None


class ChatHistoryMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None


class SaveChatRequest(BaseModel):
    session_id: str
    session_name: Optional[str] = "新对话"
    messages: List[ChatHistoryMessage]
