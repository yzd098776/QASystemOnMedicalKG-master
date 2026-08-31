"""
集中配置模块：统一读取 backend/.env 并在启动时做强制性安全校验。

注意加载顺序：本模块在 import 时先执行 load_dotenv（指向 backend/.env），
再通过 os.getenv 读取配置。app.py 顶部应最先 import 本模块，
保证 .env 先加载、后校验、再被其余模块使用。
"""

import os
import sys

from dotenv import load_dotenv

# .env 位于 backend/ 目录（本模块的上一级目录），先加载环境变量文件
_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)
load_dotenv(_ENV_PATH)


def _fail(message: str):
    """启动期配置错误：打印明确中文提示并拒绝启动"""
    print(f"[配置错误] {message}", file=sys.stderr)
    sys.exit(1)


def _read_int(name: str, default: int) -> int:
    """读取整型配置项，非法值直接拒启"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        _fail(f"{name} 必须为整数，当前值为 {raw!r}，请修正 backend/.env 后重启")


# ========== Neo4j ==========
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
# 不再兜底弱密码：未配置数据库密码时拒绝启动
if not NEO4J_PASSWORD:
    _fail(
        "NEO4J_PASSWORD 未设置，请在 backend/.env 中配置 Neo4j 数据库密码，"
        "例如：NEO4J_PASSWORD=你的数据库密码"
    )

# ========== DeepSeek ==========
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = os.getenv(
    "DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions"
)

# ========== JWT ==========
ALGORITHM = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET") or ""

# 弱值特征（小写匹配）：命中任意一项即拒绝启动
_WEAK_PATTERNS = ("change-me", "secret", "123456", "password", "example", "test")

if not JWT_SECRET:
    _fail(
        "JWT_SECRET 未设置。请在 backend/.env 中配置，可用以下命令生成强随机值：\n"
        '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )
if len(JWT_SECRET) < 32:
    _fail(
        "JWT_SECRET 长度不足 32 个字符，存在被暴力破解的风险。请重新生成：\n"
        '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )
if any(p in JWT_SECRET.lower() for p in _WEAK_PATTERNS):
    _fail(
        "JWT_SECRET 命中弱值特征（如 change-me/secret/123456/password 等），"
        "请更换为强随机值：\n"
        '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )
# 字符多样性校验：子串黑名单可被 32 位单一字符（如 32 个 a）等弱值绕过，
# 要求至少包含 8 种不同字符，进一步降低暴力破解风险
if len(set(JWT_SECRET)) < 8:
    _fail(
        "JWT_SECRET 字符多样性不足（不同字符少于 8 种，如 32 位单一字符），"
        "存在被暴力破解的风险。请重新生成强随机值：\n"
        '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
    )

# access token 有效期（分钟）与 refresh token 有效期（天），均可通过 .env 覆盖
ACCESS_TOKEN_EXPIRE_MINUTES = _read_int("ACCESS_TOKEN_EXPIRE_MINUTES", 30)
REFRESH_TOKEN_EXPIRE_DAYS = _read_int("REFRESH_TOKEN_EXPIRE_DAYS", 7)

# ========== 限流阈值（内存滑动窗口，每分钟请求数） ==========
RATE_LIMIT_AUTH_PER_MINUTE = _read_int("RATE_LIMIT_AUTH_PER_MINUTE", 10)
RATE_LIMIT_CHAT_PER_MINUTE = _read_int("RATE_LIMIT_CHAT_PER_MINUTE", 20)

# ========== 健康档案敏感字段加密密钥（提交C使用，允许为空表示明文模式） ==========
PROFILE_ENCRYPTION_KEY = os.getenv("PROFILE_ENCRYPTION_KEY") or ""
