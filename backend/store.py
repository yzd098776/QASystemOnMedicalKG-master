# coding: utf-8
"""
用户数据仓储层（阶段五分层重构）：五个 JSON 文件的加载/原子落盘、内存态、
档案敏感字段加解密读写出口、启动期加密自检与存量迁移。

自 app.py 抽出，行为与原实现逐字一致（原子写快照策略、启动拒启、明文自动加密回写）。
路由层通过 `from store import users_db, profiles_db, ...` 共享同一内存字典引用；
写入用 `store.save_json_async(...)`（模块属性调用）以便测试 monkeypatch。
"""

import os
import sys
import json
import time
import asyncio
import tempfile
import logging

from core.crypto import encrypt_field, decrypt_field, get_cipher, has_ciphertext
from core.config import (
    STORE_BACKEND, MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE,
)

logger = logging.getLogger("store")

# ========== 数据文件路径与内存态 ==========
_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(_DIR, "users.json")
PROFILES_FILE = os.path.join(_DIR, "profiles.json")
HEALTH_RECORDS_FILE = os.path.join(_DIR, "health_records.json")
HEALTH_PLANS_FILE = os.path.join(_DIR, "health_plans.json")
CHAT_HISTORY_FILE = os.path.join(_DIR, "chat_history.json")

# 健康档案敏感字段集合（过敏史/病史/家族史）：落盘前统一加密，读出时统一解密输出明文，
# 响应契约保持不变
SENSITIVE_PROFILE_FIELDS = {"allergy_drug", "allergy_food", "medical_history", "family_history"}


def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载 {path} 失败: {e}")
            return {}
    return {}


def save_json(path: str, payload: str):
    """原子写：先写同目录的“唯一”临时文件，再 os.replace 覆盖目标文件。

    竞态背景：本函数会被多个 asyncio.to_thread 线程并发调用（各写盘任务在
    线程池中真并发执行）。若使用固定临时文件名（path + ".tmp"），两个线程
    同时写同一临时文件会互相覆盖，产出损坏的 JSON；而 load_json 对损坏文件
    只记日志并返回 {}，导致重启后数据被静默清空。因此这里必须用带随机后缀的
    唯一临时文件名，保证各写任务互不干扰。
    入参 payload 为调用方已在事件循环线程内序列化好的 JSON 字符串快照，
    线程内不再触碰活字典，避免序列化期间字典被并发修改。
    """
    directory = os.path.dirname(os.path.abspath(path))
    # mkstemp 生成同目录下带随机后缀的唯一临时文件，天然避免并发写同一临时文件；
    # 以 fd 方式打开并立即关闭，避免 Windows 上临时文件被占用导致 os.replace 失败，
    # 后续用普通方式按 UTF-8 重写内容（文件已以二进制独占模式创建）
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(payload)
        # 写成功后原子替换目标文件。Windows 上临时文件刚关闭时可能被杀毒软件等
        # 短暂占用，导致 os.replace 报 WinError 5（拒绝访问），故对替换操作做
        # 短间隔有限重试；仍失败则走异常路径清理临时文件并抛出（原文件不受影响）
        last_error = None
        for _ in range(10):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except PermissionError as e:
                last_error = e
                time.sleep(0.05)
        if last_error is not None:
            raise last_error
    except OSError:
        # 异常路径：清理残留临时文件后向上抛出，交由调用方感知（不吞异常）
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


# ========== MySQL 后端分流（STORE_BACKEND=sql） ==========
# save_json_async 依「文件路径 → 表」映射把全量内存字典做事务性增量同步；
# 路由层调用方式（签名与传参）完全不变。
async def save_json_async(path: str, data: dict):
    """异步包装：阻塞的持久化操作放入线程池执行，不阻塞事件循环。

    快照策略（json 后端）：先在事件循环线程内执行 json.dumps 取得字符串快照——
    此时没有其他协程并发修改字典（协程调度不会在同步语句中间切换），再把不可变的
    字符串交给线程池写盘。若把活字典直接交给线程序列化，期间其他协程修改字典会触发
    "dictionary changed size during iteration"，或写出前后不一致的快照。

    sql 后端：事件循环线程内先做深拷贝快照（同上理由），交线程池与库做
    diff 增量同步（upsert 变更 + 删除消失），对外签名不变。
    """
    if STORE_BACKEND == "sql":
        from core import db
        table = _sql_table_for_path(path)
        snapshot = json.loads(json.dumps(data, ensure_ascii=False))
        await asyncio.to_thread(db.sync_store, table, "username", snapshot)
        return
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    await asyncio.to_thread(save_json, path, payload)


def _sql_table_for_path(path: str):
    """文件路径 → 表对象（sql 模式专用）。未知路径报错，防静默丢写。"""
    from core import db
    mapping = {
        USERS_FILE: db.users,
        PROFILES_FILE: db.profiles,
        HEALTH_RECORDS_FILE: db.health_records,
        HEALTH_PLANS_FILE: db.health_plans,
        CHAT_HISTORY_FILE: db.chat_history,
    }
    table = mapping.get(path)
    if table is None:
        raise ValueError(f"未知的存储路径: {path}")
    return table


users_db = load_json(USERS_FILE)
profiles_db = load_json(PROFILES_FILE)
health_records_db = load_json(HEALTH_RECORDS_FILE)
health_plans_db = load_json(HEALTH_PLANS_FILE)
chat_history_db = load_json(CHAT_HISTORY_FILE)
chat_sessions = {}


def _sql_startup_init():
    """sql 后端启动装配：建表（幂等）→ 装载五表进内存字典 → 登记 diff 基线快照。

    连通性/初始化失败明确报错并拒绝启动（sys.exit），绝不静默降级到 JSON——
    否则双写分叉无人察觉。
    """
    from core import db
    try:
        db.init_schema()
        loaded = {
            "users": db.load_table(db.users, "username"),
            "profiles": db.load_table(db.profiles, "username"),
            "records": db.load_table(db.health_records, "username"),
            "plans": db.load_table(db.health_plans, "username"),
            "chat": db.load_table(db.chat_history, "username"),
        }
    except Exception as e:
        print(
            f"[配置错误] STORE_BACKEND=sql 但 MySQL 初始化失败（{MYSQL_DSN_HINT}）：{e}\n"
            "请确认 MySQL 容器已启动、.env 中 MYSQL_* 连接参数正确；"
            "如需回退请在 backend/.env 设 STORE_BACKEND=json",
            file=sys.stderr,
        )
        sys.exit(1)
    # 原地替换内容（保持字典对象身份，与 JSON 装载路径行为一致）
    users_db.clear(); users_db.update(loaded["users"])
    profiles_db.clear(); profiles_db.update(loaded["profiles"])
    health_records_db.clear(); health_records_db.update(loaded["records"])
    health_plans_db.clear(); health_plans_db.update(loaded["plans"])
    chat_history_db.clear(); chat_history_db.update(loaded["chat"])
    # 登记基线快照：save_json_async 的增量 diff 以此为参照
    db.prime_snapshot(db.users, users_db)
    db.prime_snapshot(db.profiles, profiles_db)
    db.prime_snapshot(db.health_records, health_records_db)
    db.prime_snapshot(db.health_plans, health_plans_db)
    db.prime_snapshot(db.chat_history, chat_history_db)
    logger.info(
        "MySQL 存储后端就绪：users=%d profiles=%d records=%d plans=%d chat=%d",
        len(users_db), len(profiles_db), len(health_records_db),
        len(health_plans_db), len(chat_history_db),
    )


MYSQL_DSN_HINT = f"{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}" if STORE_BACKEND == "sql" else ""


def _decrypted_profile(username: str) -> dict:
    """读取用户健康档案并解密敏感字段后返回（读路径统一出口）"""
    profile = profiles_db.get(username, {})
    return {
        k: (decrypt_field(v) if k in SENSITIVE_PROFILE_FIELDS else v)
        for k, v in profile.items()
    }


def _startup_crypto_check():
    """启动自检：profiles.json 中已存在密文但未配置加密密钥时拒绝启动，
    否则存量加密数据将无法解密"""
    if get_cipher() is not None:
        return
    for profile in profiles_db.values():
        if any(has_ciphertext(profile.get(f)) for f in SENSITIVE_PROFILE_FIELDS):
            print(
                "[配置错误] profiles.json 中已存在加密数据，但未配置 PROFILE_ENCRYPTION_KEY，"
                "无法解密。请在 backend/.env 中配置原密钥后重启。",
                file=sys.stderr,
            )
            sys.exit(1)


def _migrate_profile_ciphertext():
    """存量迁移：已配置加密密钥时，把 profiles 中仍为明文的敏感字段自动加密回写"""
    if get_cipher() is None:
        return
    changed = False
    for profile in profiles_db.values():
        for field in SENSITIVE_PROFILE_FIELDS:
            value = profile.get(field)
            if isinstance(value, str) and value and not has_ciphertext(value):
                profile[field] = encrypt_field(value)
                changed = True
    if changed:
        if STORE_BACKEND == "sql":
            # sql 后端：启动期同步路径直接走线程内 diff 同步，落库而非落盘
            from core import db
            db.sync_store(db.profiles, "username", profiles_db)
        else:
            # save_json 接收已序列化的 JSON 字符串：此处为启动期同步路径，
            # 先在当前线程完成 dumps 快照再写盘，与异步路径的快照策略保持一致
            save_json(PROFILES_FILE, json.dumps(profiles_db, ensure_ascii=False, indent=2))
        logger.info("健康档案敏感字段存量加密迁移完成")


# 模块加载即执行启动自检与存量迁移（与原 app.py 顶层顺序一致）；
# sql 后端先把库内数据装载进内存字典，再做加密自检/迁移（对同一套内存态操作）
if STORE_BACKEND == "sql":
    _sql_startup_init()
_startup_crypto_check()
_migrate_profile_ciphertext()
