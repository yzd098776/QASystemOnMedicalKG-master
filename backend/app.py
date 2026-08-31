"""
医疗知识图谱智能问答系统 - FastAPI后端
基于刘焕勇的医疗知识图谱项目扩展
"""

import os
import sys
import json
import math
import time
import uuid
import asyncio
import logging
import tempfile
import traceback
import contextvars
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, EmailStr
import httpx

# 集中配置：导入时先加载 backend/.env，再校验 JWT_SECRET 等强安全项，校验失败拒启；
# 必须在读取任何环境变量之前导入，保证加载顺序正确
from core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_URL,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    RATE_LIMIT_AUTH_PER_MINUTE,
    RATE_LIMIT_CHAT_PER_MINUTE,
    ENTITY_CACHE_TTL,
)
# 安全工具：双令牌签发/校验、jti 黑名单、密码哈希加固（详见 core/security.py）
from core.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
    needs_rehash,
    revoke_jti,
    is_revoked,
    validate_access_payload,
)
# 内存滑动窗口限流（详见 core/ratelimit.py）
from core.ratelimit import check_rate_limit
# 健康档案敏感字段加密（详见 core/crypto.py）
from core.crypto import encrypt_field, decrypt_field, get_cipher, has_ciphertext
# 图谱索引/唯一约束幂等检查（阶段二，详见 core/graph_index.py）
from core.graph_index import ensure_graph_indexes
# 实体别名归一化（阶段二，详见 core/alias.py 与 backend/data/aliases.json）
from core.alias import normalize as normalize_alias, preload_alias_map
# GraphRAG 问答管线（阶段三，详见 services/ 包）
from services.graph_db import get_driver, run_cypher, run_cypher_sync, close_driver
from services import entity_recognizer, emergency as emergency_service
from services.rag_pipeline import run_graphrag_chat
from services.diagnosis_service import run_diagnosis
from services.drug_service import run_drug_interaction, run_drug_contraindication
from services.vector_index import ensure_vector_indexes
# 分层重构（阶段五）：模型 / 数据仓储 / 共享依赖抽至独立模块，app.py 专注装配与路由编排。
# save_json_async / *_db / *_FILE / _decrypted_profile / SENSITIVE_PROFILE_FIELDS 来自 store；
# 认证依赖与实体校验工具来自 deps；请求模型来自 schemas。
from store import (
    USERS_FILE, PROFILES_FILE, HEALTH_RECORDS_FILE, HEALTH_PLANS_FILE, CHAT_HISTORY_FILE,
    users_db, profiles_db, health_records_db, health_plans_db, chat_history_db,
    save_json_async, _decrypted_profile, SENSITIVE_PROFILE_FIELDS,
)
from deps import (
    get_current_user, optional_user, _current_payload,
    ALLOWED_LABELS, _validate_entity_input, _alias_candidates, _name_match_union,
    _cache_get, _cache_set,
)
from schemas import (
    RegisterRequest, LoginRequest, RefreshRequest, ProfileUpdate,
    ChatMessage, ChatRequest, DiagnosisRequest, DrugInteractionRequest,
    HealthRecord, ChatHistoryMessage, SaveChatRequest,
)

# ========== 结构化日志与请求 ID（阶段五） ==========
# 每个请求绑定唯一 request_id（透传上游 X-Request-ID 或新生成），经 contextvar
# 贯穿整条调用链，日志以 JSON 行输出，便于按请求 ID 串联一次请求的全部日志。
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    """把当前上下文 request_id 注入日志记录"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


class _JsonLogFormatter(logging.Formatter):
    """结构化日志：一行一条 JSON，含时间/级别/logger/请求ID/消息（异常含堆栈）"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "rid": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


logging.basicConfig(level=logging.INFO)
_root_logger = logging.getLogger()
_json_fmt = _JsonLogFormatter()
_rid_filter = _RequestIdFilter()
for _h in _root_logger.handlers:
    _h.setFormatter(_json_fmt)
    _h.addFilter(_rid_filter)
# 降噪：neo4j 驱动通知与 httpx 访问日志（阶段三/四已依赖，INFO 级刷屏）仅保留 WARNING+
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

if not DEEPSEEK_API_KEY:
    logger.warning("DEEPSEEK_API_KEY 未设置，AI问答将使用模拟响应")


# ========== Neo4j 连接 ==========
# 驱动与 Cypher 执行工具已抽取至 services/graph_db（阶段三），
# 本文件顶部统一从该模块导入 get_driver / run_cypher / run_cypher_sync


# ========== 缓存（阶段四：抽象见 core/cache.py） ==========
# 默认进程内 LRU（容量上限 + TTL，适配单 worker 部署）；配置 REDIS_URL 后
# 自动切换为 Redis 后端实现多实例共享。_cache_get/_cache_set 为兼容既有调用
# 的薄封装：TTL 在写入时由后端管理，_cache_get 的 ttl 参数仅作签名兼容保留。
from core.cache import (
    get_cache as _get_cache,
    entity_cache_key,
    invalidate_user_caches,
)
# 注：_cache_get/_cache_set 薄封装已并入 deps（见顶部 import）；此处仅保留缓存底层依赖。


# （用户数据仓储与持久化、启动期加密自检/迁移 已抽至 store.py，见顶部 import）


# （请求体模型已抽至 schemas.py）


# （JWT 认证依赖 已抽至 deps.py）


# ========== FastAPI 应用 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时测试Neo4j连接
    try:
        await run_cypher("RETURN 1 as test")
        print("[OK] Neo4j connected successfully")
    except Exception as e:
        print(f"[FAIL] Neo4j connection failed: {e}")
    # 启动时幂等地检查/建立七类实体的 name 索引与唯一约束（阶段二）：
    # 全部为 IF NOT EXISTS 幂等语句，重名标签自动降级为普通索引；
    # 失败仅告警不阻断启动，保证接口可用性优先
    try:
        await asyncio.to_thread(ensure_graph_indexes, run_cypher_sync)
    except Exception as e:
        logger.warning(f"启动时图谱索引/约束检查失败（不阻断启动）: {e}")
    # 启动时一次性预加载别名词典（失败仅告警降级，不阻断启动）
    try:
        logger.info(f"别名词典预加载完成，共 {preload_alias_map()} 条映射")
    except Exception as e:
        logger.warning(f"别名词典预加载失败（不阻断启动）: {e}")
    # 启动时预热实体识别词典与急症红牌表（阶段三，失败仅告警降级）
    try:
        entity_total, deny_total = entity_recognizer.preload()
        logger.info(f"实体识别词典预加载完成：{entity_total} 个实体词，{deny_total} 个否认词")
        logger.info(f"急症红牌关键词表预加载完成，共 {emergency_service.preload()} 个关键词")
    except Exception as e:
        logger.warning(f"问答词典预加载失败（不阻断启动）: {e}")
    # 启动时幂等地创建向量索引（阶段三；仅建索引不填充嵌入，
    # 填充由 scripts/build_vector_index.py 执行；失败仅告警不阻断启动）
    try:
        await asyncio.to_thread(ensure_vector_indexes, run_cypher_sync)
    except Exception as e:
        logger.warning(f"启动时向量索引检查失败（不阻断启动）: {e}")
    yield
    # 关闭时清理
    close_driver()


app = FastAPI(
    title="医疗知识图谱智能问答系统 API",
    description="基于4.4万实体+30万关系的医疗知识图谱后端接口",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """为每个请求分配/透传 X-Request-ID，绑定到日志上下文（contextvar），
    回写响应头并记录一条访问日志；同一次请求的所有日志共享同一 rid，便于串联排查。"""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    token = _request_id_var.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        # 无论成功/异常都记录访问耗时并复位上下文（异常路径交回 FastAPI 生成 500）
        dur_ms = (time.perf_counter() - start) * 1000
        logging.getLogger("access").info(
            "%s %s -> %s (%.0fms)", request.method, request.url.path, status, dur_ms
        )
        _request_id_var.reset(token)


@app.get("/health")
async def health():
    """运维探活（无认证）：验证进程存活与 Neo4j 可达（RETURN 1）；DB 不可达返回 503。"""
    try:
        await run_cypher("RETURN 1 AS ok")
    except Exception as e:
        logger.warning("健康检查失败：Neo4j 不可达: %s", e)
        raise HTTPException(status_code=503, detail="数据库不可达")
    return {"status": "ok", "database": "up"}

# ========== 路由按域注册（阶段五物理拆分至 routers/） ==========
from routers.auth import router as auth_router
from routers.user import router as user_router
from routers.kg import router as kg_router
from routers.diagnosis import router as diagnosis_router
from routers.drug import router as drug_router
from routers.guide import router as guide_router
from routers.health import router as health_router
from routers.chat import router as chat_router

for _router in (auth_router, user_router, kg_router, diagnosis_router,
                drug_router, guide_router, health_router, chat_router):
    app.include_router(_router)



# ========== 启动 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

