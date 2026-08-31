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


async def save_json_async(path: str, data: dict):
    """异步包装：阻塞的文件写入放入线程池执行，不阻塞事件循环。

    快照策略：先在事件循环线程内执行 json.dumps 取得字符串快照——此时没有
    其他协程并发修改字典（协程调度不会在同步语句中间切换），再把不可变的字符串
    交给线程池写盘。若把活字典直接交给线程序列化，期间其他协程修改字典会触发
    "dictionary changed size during iteration"，或写出前后不一致的快照。
    """
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    await asyncio.to_thread(save_json, path, payload)


users_db = load_json(USERS_FILE)
profiles_db = load_json(PROFILES_FILE)
health_records_db = load_json(HEALTH_RECORDS_FILE)
health_plans_db = load_json(HEALTH_PLANS_FILE)
chat_history_db = load_json(CHAT_HISTORY_FILE)
chat_sessions = {}


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
        # save_json 接收已序列化的 JSON 字符串：此处为启动期同步路径，
        # 先在当前线程完成 dumps 快照再写盘，与异步路径的快照策略保持一致
        save_json(PROFILES_FILE, json.dumps(profiles_db, ensure_ascii=False, indent=2))
        logger.info("健康档案敏感字段存量加密迁移完成")


# 模块加载即执行启动自检与存量迁移（与原 app.py 顶层顺序一致）
_startup_crypto_check()
_migrate_profile_ciphertext()
