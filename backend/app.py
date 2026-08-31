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


# ========== 用户认证接口 ==========
@app.post("/api/auth/register")
async def register(req: RegisterRequest, request: Request):
    # 限流：IP + 用户名双维度，防止批量注册攻击（超限抛 429）
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit("auth-ip", client_ip, RATE_LIMIT_AUTH_PER_MINUTE)
    check_rate_limit("auth-user", req.username, RATE_LIMIT_AUTH_PER_MINUTE)
    if req.username in users_db:
        raise HTTPException(status_code=400, detail="用户名已存在")
    for u in users_db.values():
        if u.get("email") == req.email:
            raise HTTPException(status_code=400, detail="邮箱已被注册")
    users_db[req.username] = {
        "username": req.username,
        "email": req.email,
        "password": hash_password(req.password),
        "token_version": 0,
        "created_at": datetime.now().isoformat(),
    }
    await save_json_async(USERS_FILE, users_db)
    return {"message": "注册成功"}


@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request):
    # 限流：IP + 用户名双维度，防止暴力破解密码（超限抛 429）
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit("auth-ip", client_ip, RATE_LIMIT_AUTH_PER_MINUTE)
    check_rate_limit("auth-user", req.username, RATE_LIMIT_AUTH_PER_MINUTE)
    user = users_db.get(req.username)
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 旧格式密码哈希登录成功后静默升级回写，存量用户无感知
    if needs_rehash(user["password"]):
        user["password"] = hash_password(req.password)
        await save_json_async(USERS_FILE, users_db)
    # 签发双令牌：access（30分钟）+ refresh（7天）；保留原 token/user 字段，
    # 新增 refresh_token 与 expires_in（秒）字段，不破坏既有响应契约
    token_version = user.get("token_version", 0)
    token = create_token(req.username, "access", extra={"token_version": token_version})
    refresh_token = create_token(req.username, "refresh", extra={"token_version": token_version})
    return {
        "token": token,
        "user": {"username": req.username, "email": user["email"]},
        "refresh_token": refresh_token,
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@app.post("/api/auth/refresh")
async def refresh(req: RefreshRequest):
    """使用 refresh token 轮换签发新的 access + refresh 令牌"""
    payload = decode_token(req.refresh_token)
    # type 缺失视为 access（兼容旧令牌），而此处只接受 refresh 类型
    if payload.get("type", "access") != "refresh":
        raise HTTPException(status_code=401, detail="无效的刷新令牌")
    username = payload.get("sub")
    user = users_db.get(username)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    jti = payload.get("jti")
    if jti and is_revoked(jti):
        raise HTTPException(status_code=401, detail="刷新令牌已失效，请重新登录")
    if payload.get("token_version", 0) != user.get("token_version", 0):
        raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")
    # 轮换：旧 refresh 的 jti 加入黑名单，防止重复使用；再签发新令牌对
    revoke_jti(jti, payload.get("exp"))
    token_version = user.get("token_version", 0)
    token = create_token(username, "access", extra={"token_version": token_version})
    refresh_token = create_token(username, "refresh", extra={"token_version": token_version})
    return {"token": token, "refresh_token": refresh_token}


@app.post("/api/auth/logout")
async def logout(request: Request):
    """登出：当前 access 的 jti 入黑名单 + 用户 token_version 自增，使存量令牌全部失效"""
    payload = _current_payload(request)
    username = payload["sub"]
    revoke_jti(payload.get("jti"), payload.get("exp"))
    user = users_db[username]
    user["token_version"] = user.get("token_version", 0) + 1
    await save_json_async(USERS_FILE, users_db)
    return {"message": "已登出"}


# ========== 健康档案接口 ==========
@app.get("/api/profile/get")
async def get_profile(username: str = Depends(get_current_user)):
    # 敏感字段解密后以明文输出，响应契约不变
    return _decrypted_profile(username)


@app.post("/api/profile/update")
async def update_profile(profile: ProfileUpdate, username: str = Depends(get_current_user)):
    data = profile.model_dump(exclude_none=True)
    # 落盘前加密敏感字段（非空时）；未配置加密密钥时 encrypt_field 原样返回明文
    for field in SENSITIVE_PROFILE_FIELDS:
        if data.get(field):
            data[field] = encrypt_field(data[field])
    profiles_db[username] = data
    await save_json_async(PROFILES_FILE, profiles_db)
    # 档案更新即时失效：清除该用户名下所有 user:{username}: 前缀缓存（含导出快照），
    # 保证紧随其后的数据导出/个性化读取看到最新档案
    invalidate_user_caches(username)
    return {"message": "健康档案已更新"}


# ========== 知识图谱接口 ==========
# （实体输入校验 / 标签白名单 / 别名候选 / 跨标签定位 Cypher 生成 已抽至 deps.py）


@app.get("/api/kg/entities")
async def search_entities(
    search: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    page: int = Query(default=1, ge=1),
    cursor: Optional[str] = Query(default=None),
):
    # 输入校验：超过200字符返回400；空搜索词视为不带关键词过滤（保持原有行为）
    safe_search = _validate_entity_input(search, "搜索词")
    safe_type = _validate_entity_input(type, "实体类型")

    # 翻页（阶段四）：保留既有 page/limit 契约；新增可选 cursor 游标参数做高效深翻页。
    # 游标依赖 name 的稳定排序（下方所有查询统一 ORDER BY n.name，走阶段二建立的有序
    # 索引；Disease 为普通索引亦支持 ORDER BY）。传 cursor 时忽略 page，用
    # name > $cursor 定位代替大 OFFSET，深翻页耗时平稳；不传时与原页码翻页等价
    # （仅额外补上 ORDER BY，使原先无序 SKIP 分页结果稳定，不改变字段契约）。
    skip = (page - 1) * limit

    async def _do_search(search_term, cursor_val):
        """按单个搜索词执行实体检索，返回 (nodes, links, total)

        cursor_val 非空 → 游标模式（WHERE n.name > $cursor 定位、不带 SKIP）；
        为空 → 页码模式（SKIP $skip LIMIT $limit）。两种模式统一 ORDER BY n.name，
        保证翻页稳定（游标定位本身依赖该全序）。
        """
        use_cursor = bool(cursor_val)
        # 分页子句：游标模式省 SKIP，页码模式保留 SKIP
        order_paging = "ORDER BY n.name " + ("" if use_cursor else "SKIP $skip ") + "LIMIT $limit"
        params = {"limit": limit}
        if use_cursor:
            params["cursor"] = cursor_val
        else:
            params["skip"] = skip

        if search_term and safe_type:
            query = (
                "MATCH (n) WHERE n.name CONTAINS $search AND $type IN labels(n) "
                + ("AND n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )
            params.update({"search": search_term, "type": safe_type})
        elif search_term:
            query = (
                "MATCH (n) WHERE n.name CONTAINS $search "
                + ("AND n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )
            params.update({"search": search_term})
        elif safe_type:
            # Neo4j 标签不可参数化，仅允许白名单内的标签拼接进 Cypher；
            # 非白名单标签返回空结果（保持既有行为兼容，不改为 400）
            if safe_type not in ALLOWED_LABELS:
                return [], [], 0
            query = (
                f"MATCH (n:{safe_type}) "
                + ("WHERE n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )
        else:
            query = (
                "MATCH (n) "
                + ("WHERE n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )

        try:
            results = await run_cypher(query, params)
        except Exception as e:
            logger.error(f"实体搜索失败: {e}")
            return [], [], 0

        inner_nodes = []
        for r in results:
            inner_nodes.append({
                "name": r["name"],
                "label": r["label"],
                "desc": r["props"].get("desc", ""),
            })

        # 获取关联边
        if len(inner_nodes) > 0:
            names = [n["name"] for n in inner_nodes[:50]]  # 限制边查询数量
            link_query = """
            MATCH (a)-[r]->(b)
            WHERE a.name IN $names AND b.name IN $names
            RETURN a.name AS source, b.name AS target, type(r) AS relType
            LIMIT 200
            """
            try:
                link_results = await run_cypher(link_query, {"names": names})
                inner_links = [{"source": l["source"], "target": l["target"], "relType": l["relType"]} for l in link_results]
            except Exception as e:
                logger.error(f"关联边查询失败: {e}")
                inner_links = []
        else:
            inner_links = []

        # 获取总数（缓存5分钟）
        cached_total = _cache_get("kg_total_count", ttl=300)
        if cached_total is not None:
            inner_total = cached_total
        else:
            count_query = "MATCH (n) RETURN count(n) AS total"
            try:
                count_result = await run_cypher(count_query)
                inner_total = count_result[0]["total"] if count_result else 0
                _cache_set("kg_total_count", inner_total)
            except Exception as e:
                logger.error(f"总数查询失败: {e}")
                inner_total = len(inner_nodes)

        return inner_nodes, inner_links, inner_total

    # 别名归一化（阶段三修正——模糊搜索两路合并）：
    # CONTAINS 模糊搜索场景下，归一化名命中的结果与原词命中的结果「合并」而非二选一：
    # 分别用规范名与原词各查一路，按 name 去重后合并返回（避免搜「感冒」只返回
    # 「上呼吸道感染」相关实体而丢失所有名称含「感冒」的实体）；
    # 合并后按原 limit 截断，保持分页语义；total 为全库实体总数，两路相同；
    # 无别名映射（候选只有原词）或无搜索词时走单路原逻辑，行为不变。
    # 精确匹配类接口不受影响，仍保持「规范名优先、未命中原词兜底」语义。
    nodes, links, total = [], [], 0
    search_terms = _alias_candidates(safe_search)
    single_path = len(search_terms) <= 1
    if single_path:
        nodes, links, total = await _do_search(search_terms[0], cursor)
    else:
        merged_nodes, merged_links = [], []
        seen_names = set()
        seen_links = set()
        for term in search_terms:
            # 别名两路合并模式下禁用游标（各路按页码取回后合并去重），故传 cursor_val=None
            t_nodes, t_links, t_total = await _do_search(term, None)
            total = t_total or total
            for n in t_nodes:
                # 按实体名去重（同名实体以先返回的一路为准，保留其标签与简介）
                if n["name"] not in seen_names:
                    seen_names.add(n["name"])
                    merged_nodes.append(n)
            for l in t_links:
                link_key = (l["source"], l["target"], l["relType"])
                if link_key not in seen_links:
                    seen_links.add(link_key)
                    merged_links.append(l)
        # 合并后按原 limit 截断（两路各自已带分页参数，合并去重后再收口到单页容量）
        nodes = merged_nodes[:limit]
        links = merged_links

    # next_cursor：仅单路、且本页取满 limit 时给出本页末实体名作为续翻游标；
    # 别名两路合并模式不做游标深翻（返回 None，页码翻页仍可用）。无更多数据时为 None。
    next_cursor = nodes[-1]["name"] if (single_path and nodes and len(nodes) >= limit) else None
    return {"nodes": nodes, "links": links, "total": total, "next_cursor": next_cursor}


@app.get("/api/kg/entity/{name}")
async def get_entity_detail(name: str):
    # 路径参数为空时按未命中处理返回 404（保持既有行为）；超长返回 400；
    # 查询本身已参数化（$name），原始值直接传入即可，无需黑名单过滤
    entity_name = _validate_entity_input(name, "实体名称")
    if not entity_name:
        raise HTTPException(status_code=404, detail="实体不存在")
    # 实体详情缓存（阶段四）：图谱为只读数据，按请求实体名缓存已构造的响应对象，
    # 命中直接返回，跳过 5 路 OPTIONAL MATCH 聚合查询；未命中（404）不缓存。
    _cache = _get_cache()
    cached_entity = _cache.get(entity_cache_key(entity_name))
    if cached_entity is not None:
        return cached_entity
    # 实体定位改为逐标签分支（_name_match_union）：原无标签 MATCH (n {name:$name})
    # 为全表扫描，逐标签后各分支走标签约束索引；后续 OPTIONAL MATCH 子句（含 s2 子句）语义不变
    query = (
        _name_match_union("n", "name") + "\n"
        "OPTIONAL MATCH (n)-[:has_symptom]->(s:Symptom)\n"
        "OPTIONAL MATCH (n)-[:common_drug]->(dr:Drug)\n"
        "OPTIONAL MATCH (n)-[:do_eat]->(f:Food)\n"
        "OPTIONAL MATCH (n)-[:need_check]->(c:Check)\n"
        "OPTIONAL MATCH (s2:Symptom)<-[:has_symptom]-(d:Disease)\n"
        "WHERE s2.name = n.name\n"
        "RETURN n, labels(n)[0] AS label,\n"
        "  collect(DISTINCT s.name) AS symptoms,\n"
        "  collect(DISTINCT dr.name) AS drugs,\n"
        "  collect(DISTINCT f.name) AS foods,\n"
        "  collect(DISTINCT c.name) AS checks,\n"
        "  collect(DISTINCT d.name) AS diseases\n"
    )
    # 别名归一化：按候选序列（规范名优先、原词兜底）逐个精确查询，命中即返回；
    # 全部未命中时保持既有 404 契约；查询异常保持既有 500 契约（仅对最后一次尝试抛出）
    results = []
    matched_name = entity_name
    for idx, candidate in enumerate(_alias_candidates(entity_name)):
        try:
            results = await run_cypher(query, {"name": candidate})
        except Exception as e:
            logger.error(f"实体详情查询失败: {e}")
            if idx == len(_alias_candidates(entity_name)) - 1:
                raise HTTPException(status_code=500, detail="查询失败")
            results = []
        if results:
            matched_name = candidate
            break

    if not results:
        raise HTTPException(status_code=404, detail="实体不存在")

    r = results[0]
    node = r["n"]
    label = r["label"]
    props = dict(node) if hasattr(node, 'items') else {}
    # 剔除向量属性 embedding（阶段三为向量检索新增），避免其流入实体详情响应与
    # 前端「全属性」通用渲染（Drug/Food 等走 properties 列表展示，向量数组会刷屏）
    props.pop("embedding", None)

    entity = {
        "name": matched_name,
        "label": label,
        "properties": props,
    }

    if label == "Disease":
        entity["symptoms"] = r["symptoms"] or []
        entity["drugs"] = r["drugs"] or []
        entity["foods"] = r["foods"] or []
        entity["checks"] = r["checks"] or []
    elif label == "Symptom":
        entity["diseases"] = r["diseases"] or []

    _cache.set(entity_cache_key(entity_name), entity, ttl=ENTITY_CACHE_TTL)
    return entity


@app.get("/api/kg/path")
async def find_path(source: str, target: str, max_depth: int = Query(default=5, ge=1, le=10)):
    # 空输入直接返回空路径结果，保持“未命中返回空结构”行为；超长返回 400；
    # source/target 通过参数化（$source/$target）传入，无需黑名单过滤
    safe_source = _validate_entity_input(source, "起始实体")
    safe_target = _validate_entity_input(target, "目标实体")
    if not safe_source or not safe_target:
        return {"paths": []}

    # Neo4j 变长关系上限不可参数化，depth 已由 FastAPI Query(ge/le) 约束为 int，
    # 此处显式强转并做范围校验，双保险防止拼接注入
    max_depth = int(max_depth)
    if not 1 <= max_depth <= 10:
        raise HTTPException(status_code=400, detail="max_depth 必须在 1 到 10 之间")

    # 起终点实体定位改为逐标签分支（_name_match_union），各分支命中标签约束索引；
    # shortestPath 在已锚定的 a、b 节点集合间展开，语义与原无标签写法一致（变长上限仍由 max_depth 控制）
    query = (
        _name_match_union("a", "source") + "\n"
        + _name_match_union("b", "target") + "\n"
        + "MATCH path = shortestPath((a)-[*.." + str(max_depth) + "]->(b))\n"
        + "RETURN [x IN nodes(path) | x.name] AS nodeNames,\n"
        + "       [r IN relationships(path) | type(r)] AS relTypes\n"
        + "LIMIT 5"
    )
    try:
        # 别名归一化：起点/终点各自按（规范名优先、原词兜底）候选展开，
        # 组合按序尝试，命中路径即返回；全部未命中保持空路径契约（最多4次查询）
        results = []
        for source_candidate in _alias_candidates(safe_source):
            for target_candidate in _alias_candidates(safe_target):
                results = await run_cypher(
                    query, {"source": source_candidate, "target": target_candidate}
                )
                if results:
                    break
            if results:
                break
    except Exception as e:
        logger.error(f"路径查询失败: {e}")
        return {"paths": []}

    paths = []
    for r in results:
        nodes = r["nodeNames"]
        edges = r["relTypes"]
        description_parts = []
        for i in range(len(edges)):
            description_parts.append(f"{nodes[i]} → {edges[i]} → {nodes[i+1]}")
        paths.append({
            "nodes": nodes,
            "edges": edges,
            "description": " → ".join(description_parts) if description_parts else "直接关联",
        })

    return {"paths": paths}


@app.get("/api/kg/related")
async def get_related_entities(entity: str, depth: int = Query(default=1, ge=1, le=3)):
    # 空输入直接返回空结构，保持既有行为；超长返回 400；实体名经参数化（$entity）传入
    safe_entity = _validate_entity_input(entity, "实体名称")
    if not safe_entity:
        return {"nodes": [], "links": []}

    # Neo4j 变长关系上限不可参数化，depth 已由 FastAPI Query(ge/le) 约束为 int，
    # 此处显式强转并做范围校验，双保险防止拼接注入
    depth = int(depth)
    if not 1 <= depth <= 3:
        raise HTTPException(status_code=400, detail="depth 必须在 1 到 3 之间")

    # 实体定位改为逐标签分支（_name_match_union），命中标签约束索引；后续展开逻辑不变
    if depth == 1:
        query = (
            _name_match_union("n", "entity") + "\n"
            "MATCH (n)-[r]-(m)\n"
            "RETURN DISTINCT m.name AS name, labels(m)[0] AS label\n"
            "LIMIT 80"
        )
    else:
        query = (
            _name_match_union("n", "entity") + "\n"
            "MATCH (n)-[*1.." + str(depth) + "]-(m)\n"
            "RETURN DISTINCT m.name AS name, labels(m)[0] AS label\n"
            "LIMIT 200"
        )

    try:
        # 别名归一化：规范名优先，原词兜底（未命中再用原词重查）
        results = []
        effective_entity = safe_entity
        for candidate in _alias_candidates(safe_entity):
            try:
                results = await run_cypher(query, {"entity": candidate})
            except Exception as e:
                logger.error(f"关联实体查询失败: {e}")
                results = []
            if results:
                effective_entity = candidate
                break
    except Exception as e:
        logger.error(f"关联实体查询失败: {e}")
        return {"nodes": [], "links": []}

    # 去重
    seen = set()
    nodes = []
    for r in results:
        if r["name"] not in seen:
            seen.add(r["name"])
            nodes.append({"name": r["name"], "label": r["label"]})

    # 获取根节点的实际标签（基于实际命中的实体名；同样走逐标签索引定位）
    root_query = _name_match_union("n", "entity") + "\nRETURN labels(n)[0] AS label LIMIT 1"
    try:
        root_result = await run_cypher(root_query, {"entity": effective_entity})
        root_label = root_result[0]["label"] if root_result else "Disease"
    except Exception:
        root_label = "Disease"
    nodes.insert(0, {"name": effective_entity, "label": root_label})

    links = []
    if len(nodes) > 1:
        names = [n["name"] for n in nodes[:50]]
        link_query = """
        MATCH (a)-[r]->(b)
        WHERE a.name IN $names AND b.name IN $names
        RETURN a.name AS source, b.name AS target, type(r) AS relType
        LIMIT 200
        """
        try:
            link_results = await run_cypher(link_query, {"names": names})
            links = [{"source": l["source"], "target": l["target"], "relType": l["relType"]} for l in link_results]
        except Exception:
            pass

    return {"nodes": nodes, "links": links}


@app.get("/api/kg/neighbors")
async def get_neighbors(
    name: str,
    depth: int = Query(default=1, ge=1, le=2),
    limit: int = Query(default=50, le=200),
):
    """增量拉取某实体的邻居子图（阶段四，供图谱页「展开下一跳」增量合并渲染）。

    与 /api/kg/related 并存（不改动后者契约）：本接口逐跳独立 LIMIT 逐层扩展，
    返回 {nodes, links}（含根节点自身），前端可合并进已有图而非整体重绘。
    - name 为空→空结构；超过200字符→400（复用 _validate_entity_input）；
    - depth 限 1|2，每跳各带一次 LIMIT（避免变长路径一次性展开爆炸）；
    - 别名归一化：规范名优先、原词兜底定位根节点；标签交替锚定 a 走标签索引。
    """
    safe_name = _validate_entity_input(name, "实体名称")
    if not safe_name:
        return {"nodes": [], "links": []}
    depth = int(depth)
    limit = int(limit)

    # 定位根实体（别名候选，规范名优先），确定实际命中名与标签；未命中→空结构
    root_label = None
    effective = safe_name
    root_query = _name_match_union("n", "entity") + "\nRETURN labels(n)[0] AS label LIMIT 1"
    for candidate in _alias_candidates(safe_name):
        try:
            rr = await run_cypher(root_query, {"entity": candidate})
        except Exception as e:
            logger.error(f"邻居根节点定位失败: {e}")
            rr = []
        if rr:
            effective = candidate
            root_label = rr[0]["label"]
            break
    if root_label is None:
        return {"nodes": [], "links": []}

    # 七类标签交替：让展开起点 a 命中阶段二建立的标签索引，避免无标签全表匹配
    label_alternation = ":" + "|".join(sorted(ALLOWED_LABELS))
    nodes = [{"name": effective, "label": root_label}]
    links = []
    seen = {effective}
    frontier = [effective]
    for _ in range(depth):
        if not frontier:
            break
        # 从当前层沿无向关系扩展一跳，排除已收集节点，本跳独立 LIMIT
        hop_query = (
            f"MATCH (a{label_alternation})-[r]-(b)\n"
            "WHERE a.name IN $names AND NOT b.name IN $visited\n"
            "RETURN b.name AS name, labels(b)[0] AS label, a.name AS from_name,\n"
            "       type(r) AS relType\n"
            "LIMIT $limit"
        )
        try:
            rows = await run_cypher(hop_query, {"names": frontier, "visited": list(seen), "limit": limit})
        except Exception as e:
            logger.error(f"邻居扩展失败: {e}")
            break
        new_frontier = []
        for r in rows:
            nm = r.get("name")
            if not nm or nm in seen:
                continue
            seen.add(nm)
            nodes.append({"name": nm, "label": r.get("label")})
            links.append({"source": r["from_name"], "target": nm, "relType": r.get("relType")})
            new_frontier.append(nm)
        frontier = new_frontier

    return {"nodes": nodes, "links": links}


# ========== 疾病自查接口 ==========
@app.post("/api/diagnosis")
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


# ========== 用药安全接口 ==========
@app.get("/api/drug/contraindication")
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


@app.get("/api/food/contraindication")
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


@app.post("/api/drug/interaction")
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


# ========== 就医指南接口 ==========
@app.get("/api/guide/department")
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


@app.get("/api/guide/check")
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


# ========== 健康管理接口 ==========
@app.post("/api/health/prevention")
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


@app.post("/api/health/chronic")
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


@app.get("/api/health/records")
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


@app.post("/api/health/records")
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


@app.delete("/api/health/records/{record_id}")
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
@app.get("/api/health/plans")
async def get_health_plans(username: str = Depends(get_current_user)):
    plans = health_plans_db.get(username, [])
    return {"plans": plans}


@app.post("/api/health/plans")
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


@app.delete("/api/health/plans")
async def clear_health_plans(username: str = Depends(get_current_user)):
    health_plans_db[username] = []
    await save_json_async(HEALTH_PLANS_FILE, health_plans_db)
    return {"message": "所有计划已清空"}


# ========== 知识百科接口 ==========
@app.get("/api/wiki/daily-tip")
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


@app.post("/api/chat")
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

@app.get("/api/chat/history")
async def get_chat_history(username: str = Depends(get_current_user)):
    """获取当前用户的所有聊天记录"""
    user_history = chat_history_db.get(username, {"sessions": []})
    return user_history


@app.post("/api/chat/history/save")
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


@app.delete("/api/chat/history")
async def clear_chat_history(username: str = Depends(get_current_user)):
    """一键清除当前用户所有聊天记录"""
    chat_history_db[username] = {"sessions": []}
    await save_json_async(CHAT_HISTORY_FILE, chat_history_db)
    return {"ok": True}


# ========== 用户数据导出与彻底删除 ==========
@app.get("/api/user/export")
async def export_user_data(username: str = Depends(get_current_user)):
    """聚合导出当前用户全部数据：账户信息（不含密码字段）、健康档案（解密后）、
    健康记录、健康计划、聊天记录；缺失处给空对象/空数组"""
    # 导出数据缓存（阶段四）：聚合五处数据 + 敏感字段解密有额外开销，按用户缓存短 TTL 快照。
    # 档案更新与删号即时失效（见 update_profile / delete_user_data），
    # 记录/计划/聊天等其他数据写入以 TTL 最终一致（键前缀 user:{username}: 与失效约定一致）。
    cache = _get_cache()
    cache_key = f"user:{username}:export"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    user_info = {
        k: v for k, v in users_db.get(username, {}).items() if k != "password"
    }
    result = {
        "username": username,
        "user": user_info,
        "profile": _decrypted_profile(username),
        "health_records": health_records_db.get(username, []),
        "health_plans": health_plans_db.get(username, []),
        "chat_history": chat_history_db.get(username, {"sessions": []}),
    }
    cache.set(cache_key, result, ttl=30)
    return result


@app.delete("/api/user/data")
async def delete_user_data(username: str = Depends(get_current_user)):
    """删除账号及该用户在五处存储中的全部数据并回写文件；
    删除前 token_version 自增，使存量令牌全部失效"""
    user = users_db.get(username)
    if user:
        user["token_version"] = user.get("token_version", 0) + 1
    users_db.pop(username, None)
    profiles_db.pop(username, None)
    health_records_db.pop(username, None)
    health_plans_db.pop(username, None)
    chat_history_db.pop(username, None)
    await save_json_async(USERS_FILE, users_db)
    await save_json_async(PROFILES_FILE, profiles_db)
    await save_json_async(HEALTH_RECORDS_FILE, health_records_db)
    await save_json_async(HEALTH_PLANS_FILE, health_plans_db)
    await save_json_async(CHAT_HISTORY_FILE, chat_history_db)
    # 删号即时失效该用户所有缓存（导出快照等），避免删除后仍能读到已清除数据的缓存视图
    invalidate_user_caches(username)
    return {"message": "账号及全部数据已删除"}


# ========== 启动 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
