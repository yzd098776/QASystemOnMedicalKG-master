# coding: utf-8
"""
MySQL 存储层（阶段 A：五处 JSON → MySQL 8）。

设计要点：
- SQLAlchemy 2.0 **Core**（sa.Table + select/insert），不引入 ORM 实体类——
  项目已有 Pydantic 模型层；
- 驱动为同步 PyMySQL（jti 黑名单的 is_revoked/revoke_jti 处于同步调用链，
  异步引擎无法在同步上下文直调；写路径由 store.save_json_async 经
  asyncio.to_thread 包裹，对外函数签名不变）；
- 连接池开 pool_pre_ping（防 WSL 休眠后的失效连接）+ pool_recycle（防
  wait_timeout 断连）；
- 表结构：users 主表 + 四张子表（档案/记录/计划/聊天）外键 ON DELETE CASCADE
  （删号一条语句自动清干净）；档案敏感字段保持 Fernet 密文原样搬运，
  本层不做任何加解密；
- jti_blacklist 入表：解决登出黑名单进程内不共享的多 worker 隐患，
  重启后旧令牌依旧失效；
- entity_embeddings 表为阶段 B 预留：主键含 (provider, model_ver)，
  换模型时天然并存、可精准失效；embedding 以 float32 BLOB 存储；
- 值保真：每条记录以 JSON 列整存（与 JSON 文件中的 per-user 值一一对应），
  读出即原形状，响应字段零漂移。

地址不写死：连接参数全部来自 .env（core.config），容器内互访用服务名、
容器外用 127.0.0.1，由 MYSQL_HOST 控制。
"""

import json
import logging
import threading
import time

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import insert as mysql_insert

from .config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
)

logger = logging.getLogger("core.db")

# ========== 表定义（sa.Table 直接映射，无 ORM） ==========
metadata = sa.MetaData()

_users_pk = sa.Column("username", sa.String(64), primary_key=True)

users = sa.Table(
    "users", metadata,
    _users_pk,
    sa.Column("data", sa.JSON, nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.func.now(),
              onupdate=sa.func.now()),
    mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)


def _child_table(name: str) -> sa.Table:
    """子表工厂：username 主键 + 外键级联删除 + JSON 整存数据列"""
    return sa.Table(
        name, metadata,
        sa.Column("username", sa.String(64),
                  sa.ForeignKey("users.username", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.func.now(),
                  onupdate=sa.func.now()),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )


profiles = _child_table("user_profiles")
health_records = _child_table("health_records")
health_plans = _child_table("health_plans")
chat_history = _child_table("chat_history")

# 登出黑名单入表：jti -> 令牌原始过期时间戳（秒），到期后由清理任务删除
jti_blacklist = sa.Table(
    "jti_blacklist", metadata,
    sa.Column("jti", sa.String(64), primary_key=True),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("revoked_at", sa.TIMESTAMP, server_default=sa.func.now()),
    sa.Index("idx_jti_expires", "expires_at"),
    mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)

# 实体语义嵌入表（阶段 B 预留）：主键含 provider + model_ver，换模型并存、精准失效
entity_embeddings = sa.Table(
    "entity_embeddings", metadata,
    sa.Column("key_hash", sa.CHAR(64), primary_key=True),   # sha256(label \x1f name)
    sa.Column("label", sa.String(32), nullable=False),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("provider", sa.String(32), primary_key=True),
    sa.Column("model_ver", sa.String(32), primary_key=True),
    sa.Column("dim", sa.SmallInteger, nullable=False),
    sa.Column("embedding", sa.LargeBinary, nullable=False),  # float32 小端序
    sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.func.now(),
              onupdate=sa.func.now()),
    mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)

# ========== 引擎与会话（模块级懒建，单例） ==========
_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """进程级单例引擎。连接参数走 .env；connect_timeout 防配置错误时挂死启动"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = sa.engine.URL.create(
                    "mysql+pymysql",
                    username=MYSQL_USER, password=MYSQL_PASSWORD,
                    host=MYSQL_HOST, port=MYSQL_PORT, database=MYSQL_DATABASE,
                )
                _engine = sa.create_engine(
                    url,
                    pool_pre_ping=True,   # 借出前探活，避免拿到已断连接
                    pool_recycle=280,     # 短于常见 wait_timeout，防陈旧连接
                    pool_size=5, max_overflow=10,
                    connect_args={"connect_timeout": 5, "charset": "utf8mb4"},
                )
    return _engine


def init_schema():
    """幂等建表（IF NOT EXISTS 语义），阶段 A 全部表一次到位（含阶段 B 预留表）"""
    metadata.create_all(get_engine(), checkfirst=True)


def _ser(value) -> str:
    """稳定的序列化形式，仅用于 diff 判断变更，与落库内容无关"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# 每张表的「上次已同步快照」与写锁：save_json_async 传的是全量内存字典，
# 与快照做顶层键 diff，只 upsert 变更行 / 删除消失行，事务内提交。
_SNAPSHOTS: dict[str, dict[str, str]] = {}
_TABLE_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _table_lock(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _TABLE_LOCKS.setdefault(name, threading.Lock())


def prime_snapshot(table: sa.Table, data: dict):
    """装载内存数据后登记基线快照（diff 的参照系）"""
    _SNAPSHOTS[table.name] = {k: _ser(v) for k, v in data.items()}


def sync_store(table: sa.Table, pk: str, data: dict):
    """把内存字典与快照 diff 后事务性同步到表（供 asyncio.to_thread 调用）。

    - 变更/新增键 → upsert；快照中存在而 data 中消失的键 → 删除
      （users 表删除会经外键级联清空该用户在四张子表中的行）；
    - 失败抛异常交由调用方感知（与 JSON 原子写失败时的行为一致），
      不静默跳过，避免内存与库分叉。
    """
    lock = _table_lock(table.name)
    with lock:
        prev = _SNAPSHOTS.get(table.name, {})
        cur = {k: _ser(v) for k, v in data.items()}
        changed = [k for k, v in cur.items() if prev.get(k) != v]
        removed = [k for k in prev if k not in cur]
        if not changed and not removed:
            return 0
        with get_engine().begin() as conn:
            # 分块删除（IN 子句避免超长语句）
            for i in range(0, len(removed), 500):
                conn.execute(sa.delete(table).where(
                    table.c[pk].in_(removed[i:i + 500])))
            for key in changed:
                stmt = mysql_insert(table).values(**{pk: key, "data": data[key]})
                stmt = stmt.on_duplicate_key_update(data=stmt.inserted.data)
                conn.execute(stmt)
        _SNAPSHOTS[table.name] = cur
        logger.debug("同步 %s：upsert %d，delete %d", table.name, len(changed), len(removed))
        return len(changed) + len(removed)


def load_table(table: sa.Table, pk: str) -> dict:
    """整表装载为 {键: 值} 内存字典（值即 JSON 列反序列化结果，形状与 JSON 文件一致）"""
    with get_engine().connect() as conn:
        rows = conn.execute(sa.select(table.c[pk], table.c.data)).fetchall()
    return {r[0]: r[1] for r in rows}


def table_count(table: sa.Table) -> int:
    with get_engine().connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(table)).scalar()


# ========== jti 黑名单读写（同步接口，供 core.security 直调） ==========


def jti_revoke(jti: str, exp_ts: float):
    stmt = mysql_insert(jti_blacklist).values(jti=jti, expires_at=exp_ts)
    stmt = stmt.on_duplicate_key_update(expires_at=stmt.inserted.expires_at)
    with get_engine().begin() as conn:
        conn.execute(stmt)


def jti_active() -> dict[str, float]:
    """当前未过期的黑名单条目 {jti: expires_at}（表规模受令牌有效期约束，很小）"""
    with get_engine().connect() as conn:
        rows = conn.execute(sa.select(jti_blacklist.c.jti, jti_blacklist.c.expires_at)
                            .where(jti_blacklist.c.expires_at > time.time())).fetchall()
    return {r[0]: r[1] for r in rows}


def jti_purge_expired():
    with get_engine().begin() as conn:
        conn.execute(sa.delete(jti_blacklist).where(
            jti_blacklist.c.expires_at <= time.time()))
