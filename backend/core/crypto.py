"""
健康档案敏感字段加密模块：基于 cryptography.fernet 的字段级加解密。

设计要点：
- 密钥读配置项 PROFILE_ENCRYPTION_KEY（.env，生成命令见 .env.example）；
- 密文统一带 `enc:` 前缀；解密对无前缀值原样返回（兼容历史明文数据）；
- 密钥缺失时 get_cipher() 返回 None，进入明文模式并仅告警一次；
- 启动自检由 app.py 完成：若 profiles.json 中已存在 `enc:` 密文但密钥缺失，拒绝启动。
"""

import logging
import threading

from cryptography.fernet import Fernet, InvalidToken

from .config import PROFILE_ENCRYPTION_KEY

logger = logging.getLogger(__name__)

# 密文前缀：用于区分已加密字段与历史明文字段
ENC_PREFIX = "enc:"

_cipher = None
_cipher_warned = False
_cipher_lock = threading.Lock()


def get_cipher():
    """获取 Fernet 实例；密钥缺失时返回 None（明文模式），并仅告警一次"""
    global _cipher, _cipher_warned
    if _cipher is not None:
        return _cipher
    with _cipher_lock:
        if _cipher is not None:
            return _cipher
        if not PROFILE_ENCRYPTION_KEY:
            if not _cipher_warned:
                logger.warning(
                    "PROFILE_ENCRYPTION_KEY 未配置，健康档案敏感字段将以明文存储。"
                    "生成密钥：python -c \"from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())\""
                )
                _cipher_warned = True
            return None
        _cipher = Fernet(PROFILE_ENCRYPTION_KEY.encode("ascii"))
        return _cipher


def encrypt_field(plain):
    """加密单个字段值，返回带 `enc:` 前缀的密文。

    - 空值（None/空字符串）原样返回；
    - 已是密文（带前缀）原样返回，保证幂等；
    - 无密钥时原样返回明文（明文模式）。
    """
    if plain is None or plain == "":
        return plain
    if isinstance(plain, str) and plain.startswith(ENC_PREFIX):
        return plain
    cipher = get_cipher()
    if cipher is None:
        return plain
    token = cipher.encrypt(str(plain).encode("utf-8"))
    return ENC_PREFIX + token.decode("ascii")


def decrypt_field(value):
    """解密单个字段值。

    - 非字符串或不带 `enc:` 前缀的值原样返回（明文兼容）；
    - 解密失败（如密钥不匹配）时记录错误并原样返回，避免读路径整体崩溃。
    """
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value
    cipher = get_cipher()
    if cipher is None:
        # 理论上启动自检会拦截“有密文但无密钥”的情况，此处兜底
        logger.error("发现加密字段但未配置 PROFILE_ENCRYPTION_KEY，无法解密")
        return value
    try:
        return cipher.decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        logger.error(f"敏感字段解密失败（密钥可能不匹配）: {e}")
        return value


def has_ciphertext(value) -> bool:
    """判断字段值是否为 `enc:` 前缀密文"""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)
