# coding: utf-8
"""认证路由（阶段五物理拆分）：注册 / 登录 / 刷新 / 登出。路径契约不变。"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from core.config import ACCESS_TOKEN_EXPIRE_MINUTES, RATE_LIMIT_AUTH_PER_MINUTE
from core.security import (
    create_token, decode_token, hash_password, verify_password,
    needs_rehash, revoke_jti, is_revoked,
)
from core.ratelimit import check_rate_limit
from store import users_db, save_json_async, USERS_FILE
from deps import _current_payload
from schemas import RegisterRequest, LoginRequest, RefreshRequest

router = APIRouter()
logger = logging.getLogger("app.auth")


# ========== 用户认证接口 ==========
@router.post("/api/auth/register")
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


@router.post("/api/auth/login")
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


@router.post("/api/auth/refresh")
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


@router.post("/api/auth/logout")
async def logout(request: Request):
    """登出：当前 access 的 jti 入黑名单 + 用户 token_version 自增，使存量令牌全部失效"""
    payload = _current_payload(request)
    username = payload["sub"]
    revoke_jti(payload.get("jti"), payload.get("exp"))
    user = users_db[username]
    user["token_version"] = user.get("token_version", 0) + 1
    await save_json_async(USERS_FILE, users_db)
    return {"message": "已登出"}
