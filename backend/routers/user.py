# coding: utf-8
"""用户与档案路由（阶段五物理拆分）：健康档案读写、数据导出与彻底删除。"""
from fastapi import APIRouter, Depends, HTTPException

from core.crypto import encrypt_field
from core.cache import get_cache as _get_cache, invalidate_user_caches
from store import (
    users_db, profiles_db, health_records_db, health_plans_db, chat_history_db,
    save_json_async, _decrypted_profile, SENSITIVE_PROFILE_FIELDS,
    USERS_FILE, PROFILES_FILE, HEALTH_RECORDS_FILE, HEALTH_PLANS_FILE, CHAT_HISTORY_FILE,
)
from deps import get_current_user
from schemas import ProfileUpdate

router = APIRouter()


# ========== 健康档案接口 ==========
@router.get("/api/profile/get")
async def get_profile(username: str = Depends(get_current_user)):
    # 敏感字段解密后以明文输出，响应契约不变
    return _decrypted_profile(username)


@router.post("/api/profile/update")
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


# ========== 用户数据导出与彻底删除 ==========
@router.get("/api/user/export")
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


@router.delete("/api/user/data")
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
