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


def _read_float(name: str, default: float) -> float:
    """读取浮点配置项，非法值直接拒启"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        _fail(f"{name} 必须为数字，当前值为 {raw!r}，请修正 backend/.env 后重启")


def _read_bool(name: str, default: bool) -> bool:
    """读取布尔配置项（1/true/yes/on 视为真，大小写不敏感）"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


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

# ========== 用户数据存储后端（MySQL 迁移，可回滚） ==========
# json：五处 JSON 文件存储（默认，回滚路径）；sql：MySQL 8（SQLAlchemy Core）。
# 改一个环境变量即可回退，两种取值下 /api 契约完全一致。
STORE_BACKEND = (os.getenv("STORE_BACKEND") or "json").strip().lower()
if STORE_BACKEND not in ("json", "sql"):
    _fail(
        f"STORE_BACKEND 只能为 json 或 sql，当前值为 {STORE_BACKEND!r}，"
        "请修正 backend/.env 后重启"
    )

# MySQL 连接参数：仅 STORE_BACKEND=sql 时生效并强校验。
# 地址不写死：WSL 宿主机上跑后端取 127.0.0.1；后端进容器后改为服务名 mysql。
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = _read_int("MYSQL_PORT", 3306)
MYSQL_USER = os.getenv("MYSQL_USER", "kguser")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD") or ""
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "medicalkg")
if STORE_BACKEND == "sql" and not MYSQL_PASSWORD:
    _fail(
        "STORE_BACKEND=sql 时必须配置 MYSQL_PASSWORD（kguser 专用账号，"
        "只授业务库增删改查），请在 backend/.env 中设置后重启"
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

# ========== GraphRAG 问答管线（阶段三） ==========
# 混合检索双路权重：关键词路（别名+CONTAINS）与向量路（余弦相似），
# 两者建议合计为 1.0（非强制，融合时按加权和排序）
HYBRID_KEYWORD_WEIGHT = _read_float("HYBRID_KEYWORD_WEIGHT", 0.6)
HYBRID_VECTOR_WEIGHT = _read_float("HYBRID_VECTOR_WEIGHT", 0.4)

# 向量嵌入维度（哈希字符 n-gram 嵌入，须与建索引/写入维度一致，改动后需重建索引）
EMBEDDING_DIM = _read_int("EMBEDDING_DIM", 256)

# 向量索引名前缀（按标签生成 {前缀}_disease / {前缀}_drug / {前缀}_symptom）
VECTOR_INDEX_PREFIX = os.getenv("VECTOR_INDEX_PREFIX", "kg_embedding")

# Text2Cypher 长尾覆盖：总开关与只读执行超时（秒）
TEXT2CYPHER_ENABLED = _read_bool("TEXT2CYPHER_ENABLED", True)
TEXT2CYPHER_TIMEOUT = _read_int("TEXT2CYPHER_TIMEOUT", 10)

# ========== 语义嵌入升级（阶段 B） ==========
# 提供方：hash（默认，零外部调用）| zhipu | tongyi | qwen（OpenAI 兼容 embeddings，
# 配合 EMBED_API_BASE_URL + EMBEDDING_MODEL 指向专属部署）。远程 API 未配置/失败/超时
# 一律自动降级为哈希嵌入（打 WARNING 不抛异常），检索与问答照常返回。
EMBEDDING_PROVIDER = (os.getenv("EMBEDDING_PROVIDER") or "hash").strip().lower()
if EMBEDDING_PROVIDER not in ("hash", "zhipu", "tongyi", "qwen"):
    _fail(
        "EMBEDDING_PROVIDER 只能为 hash / zhipu / tongyi / qwen，"
        f"当前值为 {EMBEDDING_PROVIDER!r}"
    )
# 模型名覆盖（如专属部署上的 qwen3.7-text-embedding；留空用各 provider 默认）
EMBEDDING_MODEL = (os.getenv("EMBEDDING_MODEL") or "").strip()
# qwen 专用每千条单价（元，dry-run 预估用）
EMBED_COST_PER_1K_QWEN = _read_float("EMBED_COST_PER_1K_QWEN", 0.0005)
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY") or ""
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY") or ""
# 可选：嵌入 API 基址覆盖（测试代理/私有网关场景；留空用各家官方地址）
EMBED_API_BASE_URL = (os.getenv("EMBED_API_BASE_URL") or "").strip()
# 模型版本标识：入 entity_embeddings 主键，换模型时新旧嵌入并存、可精准失效
EMBEDDING_MODEL_VER = os.getenv("EMBEDDING_MODEL_VER") or ""
# 语义嵌入维度（vector-1.0 上限 2048、2.0 上限 4096，512 远离上限且存储可控）
SEMANTIC_EMBEDDING_DIM = _read_int("SEMANTIC_EMBEDDING_DIM", 512)
# 查询嵌入：短 TTL 缓存 + 远程超时（超时即降级哈希，不拖慢问答首帧）
EMBED_CACHE_TTL = _read_int("EMBED_CACHE_TTL", 60)
EMBED_TIMEOUT_SECONDS = _read_float("EMBED_TIMEOUT_SECONDS", 2.0)
# 熔断：连续 N 次失败后冷却 M 秒（冷却期内直接走哈希兜底，不打远程）
EMBED_CIRCUIT_FAILURES = _read_int("EMBED_CIRCUIT_FAILURES", 3)
EMBED_CIRCUIT_COOLDOWN = _read_int("EMBED_CIRCUIT_COOLDOWN", 60)
# 建索引 dry-run 费用熔断阈值（元）
EMBEDDING_MAX_COST_YUAN = _read_float("EMBEDDING_MAX_COST_YUAN", 10.0)
# 每千条单价（元，用于 dry-run 费用预估）
EMBED_COST_PER_1K_ZHIPU = _read_float("EMBED_COST_PER_1K_ZHIPU", 0.0005)
EMBED_COST_PER_1K_TONGYI = _read_float("EMBED_COST_PER_1K_TONGYI", 0.0007)

# ========== 缓存与性能（阶段四） ==========
# Redis 连接串：为空表示不启用，缓存走进程内 LRU（默认单 worker 部署）；
# 非空则尝试用 Redis 后端，使多 worker/多实例共享缓存（需 pip install "redis[hiredis]"）
REDIS_URL = (os.getenv("REDIS_URL") or "").strip()

# 本地 LRU 容量上限（超出淘汰最久未使用项）与默认存活时间（秒）
CACHE_MAX_ENTRIES = _read_int("CACHE_MAX_ENTRIES", 1024)
CACHE_DEFAULT_TTL = _read_int("CACHE_DEFAULT_TTL", 300)

# 实体详情缓存存活时间（秒）：图谱为只读数据，短 TTL 兼顾新鲜度与命中率
ENTITY_CACHE_TTL = _read_int("ENTITY_CACHE_TTL", 300)

# 慢查询日志阈值（毫秒）：run_cypher 执行耗时超过该值时记 WARNING，便于定位性能热点
SLOW_QUERY_MS = _read_int("SLOW_QUERY_MS", 200)
