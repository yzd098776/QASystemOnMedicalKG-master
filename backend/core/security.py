"""
安全模块：双令牌（access/refresh）签发与校验、jti 黑名单、密码哈希加固。

设计要点：
- access token 默认 30 分钟、refresh token 默认 7 天（时长读配置）；
- 登出即时失效：进程内 jti 黑名单（惰性清理）+ 用户记录 token_version 版本比对；
- 密码哈希：明文 >72 字节时先做 sha256 摘要再交 bcrypt（bcrypt 仅取前 72 字节，
  先摘要避免超长密码被静默截断），新哈希带 `v2$` 前缀；旧哈希按原逻辑校验，
  登录成功后由调用方静默升级回写，存量用户零影响。
"""

import hashlib
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import HTTPException
from jose import JWTError, jwt

from .config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    JWT_SECRET,
    REFRESH_TOKEN_EXPIRE_DAYS,
    STORE_BACKEND,
)

logger = logging.getLogger("core.security")

# ========== 密码哈希 ==========

# 新格式哈希前缀：表示已按“超长密码先 sha256 摘要”的加固规则生成
_V2_PREFIX = "v2$"


def _password_bytes(password: str) -> bytes:
    """UTF-8 编码；超过 72 字节时先做 sha256 摘要，避免 bcrypt 静默截断"""
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raw = hashlib.sha256(raw).digest()
    return raw


def hash_password(password: str) -> str:
    """生成新格式密码哈希（带 v2$ 前缀）"""
    hashed = bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt())
    return _V2_PREFIX + hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """按前缀分支校验：v2$ 走加固逻辑，无前缀走原逻辑（兼容存量哈希）"""
    try:
        if hashed.startswith(_V2_PREFIX):
            return bcrypt.checkpw(
                _password_bytes(plain), hashed[len(_V2_PREFIX):].encode("utf-8")
            )
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # 哈希格式非法时视为校验失败
        return False


def needs_rehash(hashed: str) -> bool:
    """判断是否为旧格式哈希（无前缀），用于登录成功后静默升级"""
    return not hashed.startswith(_V2_PREFIX)


# ========== JWT 签发与校验 ==========


def create_token(subject: str, token_type: str = "access",
                 expires_delta: timedelta = None, extra: dict = None) -> str:
    """签发 JWT。

    声明包含：sub（用户名）、type（access/refresh）、jti（唯一ID，用于登出黑名单）、
    token_version（由调用方通过 extra 传入，用于登出后整批失效）、iat、exp。
    """
    now = datetime.now(timezone.utc)
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
            if token_type == "access"
            else timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        )
    payload = {
        "sub": subject,
        "type": token_type,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并校验签名与有效期，失败返回 401"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的认证令牌")


def validate_access_payload(payload: dict, users_db: dict) -> str:
    """校验 access token 载荷，返回用户名；不合法一律 401。

    规则：
    - type 缺失视为 access（兼容旧版 7 天 token），非 access 类型拒绝；
    - jti 命中黑名单（已登出）拒绝；
    - token_version 缺失视为 0，与 users_db 中当前值不一致则拒绝（登出即失效）。
    """
    username = payload.get("sub")
    if not username or username not in users_db:
        raise HTTPException(status_code=401, detail="用户不存在")

    token_type = payload.get("type", "access")
    if token_type != "access":
        raise HTTPException(status_code=401, detail="无效的认证令牌")

    jti = payload.get("jti")
    if jti and is_revoked(jti):
        raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")

    current_version = users_db[username].get("token_version", 0)
    if payload.get("token_version", 0) != current_version:
        raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")

    return username


# ========== jti 黑名单（json：纯进程内；sql：DB 为准 + 进程内镜像） ==========

# jti -> 过期时间戳（秒）；get 时惰性清理过期项，避免无限增长。
# sql 后端下本字典是 jti_blacklist 表的读镜像（限频刷新），写路径双写：
# 解决进程内黑名单多 worker 不共享、重启即清零的问题（登出/换票后旧令牌
# 跨进程、跨重启依旧失效）。
_BLACKLIST: dict[str, float] = {}
_BLACKLIST_LOCK = threading.Lock()

# 镜像自 DB 的刷新限频（秒）：黑名单规模受令牌有效期约束很小，全量拉取即可
_JTI_SYNC_SECONDS = 5.0
_jti_last_sync = 0.0


def _purge_expired(now: float):
    """惰性清理已过期的黑名单条目（调用方需持有锁）"""
    expired = [jti for jti, exp_ts in _BLACKLIST.items() if exp_ts <= now]
    for jti in expired:
        del _BLACKLIST[jti]


def revoke_jti(jti: str, exp_ts: float = None):
    """将 jti 加入黑名单；exp_ts 为该令牌原本的过期时间戳（用于惰性清理）。

    sql 后端下写 DB 失败直接抛出（与业务数据写失败的行为一致），
    不静默吞掉——否则会造成「以为登出了实际没登出」的安全假象。
    """
    if not jti:
        return
    exp = exp_ts if exp_ts else time.time() + 7 * 24 * 3600
    if STORE_BACKEND == "sql":
        from . import db
        db.jti_revoke(jti, exp)
    with _BLACKLIST_LOCK:
        _purge_expired(time.time())
        _BLACKLIST[jti] = exp


def is_revoked(jti: str) -> bool:
    """查询 jti 是否已被吊销，顺带清理过期条目。

    sql 后端：镜像命中即真；未命中且超过限频窗口则先从 DB 全量刷新未过期
    条目再判定（覆盖重启后旧令牌、其他 worker 登出两种场景）；
    DB 刷新失败仅记 WARNING 并按镜像判定，不阻断认证链路。
    """
    if not jti:
        return False
    global _jti_last_sync
    now = time.time()
    with _BLACKLIST_LOCK:
        _purge_expired(now)
        if jti in _BLACKLIST:
            return True
        if STORE_BACKEND == "sql" and now - _jti_last_sync >= _JTI_SYNC_SECONDS:
            try:
                from . import db
                _jti_last_sync = time.time()
                remote = db.jti_active()
                db.jti_purge_expired()
            except Exception as e:  # noqa: BLE001 降级：DB 不可用时按镜像判定
                logger.warning("jti 黑名单刷新失败，暂用本地镜像判定: %s", e)
            else:
                _BLACKLIST.clear()
                _BLACKLIST.update(remote)
                return jti in _BLACKLIST
        return False
