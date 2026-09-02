# coding: utf-8
"""
语义嵌入 Provider 抽象（阶段 B：哈希嵌入 → 真实语义嵌入升级）。

三个实现，统一接口 embed(texts) -> list[list[float]]：
- HashProvider：纯 Python 哈希字符 n-gram（现有实现，零外部依赖，兜底路径）；
- ZhipuProvider：智谱 embedding-3，REST 调用（httpx），批次 64 条；
- TongyiProvider：通义 text-embedding-v3，OpenAI 兼容 REST（httpx），批次 10 条。

强制约束落点（不可协商项 3/4/7）：
- 零重型依赖：只复用项目已有 httpx 走 REST，禁止 torch/各家 SDK；
- 降级不中断：未配 API Key / 请求失败 / 超时（默认 2s）→ 自动回退哈希嵌入，
  打 WARNING 不抛异常，检索与问答照常返回；
- 连续失败熔断：N 次连续失败后冷却 M 秒，冷却期内直接走哈希、不打远程；
- 成本可控：dry_run_report() 输出实体数 / 预估 tokens / 预估费用，
  超过 EMBEDDING_MAX_COST_YUAN 时中止提示（由建索引脚本执行中止）。

model_ver 标识：入 MySQL entity_embeddings 主键，换模型时新旧向量并存、可精准失效。
"""

import hashlib
import json
import logging
import os
import struct
import threading
import time

import httpx

from core.config import (
    EMBEDDING_DIM, EMBEDDING_PROVIDER, EMBEDDING_MODEL_VER, EMBEDDING_MODEL,
    EMBED_API_BASE_URL,
    SEMANTIC_EMBEDDING_DIM, ZHIPU_API_KEY, DASHSCOPE_API_KEY,
    EMBED_TIMEOUT_SECONDS, EMBED_CACHE_TTL,
    EMBED_CIRCUIT_FAILURES, EMBED_CIRCUIT_COOLDOWN,
    EMBED_COST_PER_1K_ZHIPU, EMBED_COST_PER_1K_TONGYI, EMBED_COST_PER_1K_QWEN,
)
from .vector_index import embed_hash

logger = logging.getLogger(__name__)


# ========== 哈希嵌入兜底 ==========

class HashProvider:
    """现有纯 Python 哈希字符 n-gram 嵌入（升级前后行为基线，也是所有降级路径的兜底）"""

    name = "hash"

    def __init__(self, dim: int = None):
        self.dim = dim or EMBEDDING_DIM

    @property
    def model_ver(self) -> str:
        return f"hash-blake2b-d{self.dim}"

    batch_size = 500  # 本地计算，无批次约束，仅控制单次内存占用

    def embed(self, texts, strict=False, fallback_dim=None):
        # 哈希提供者本身即兜底；fallback_dim 仅在降级语义上无意义，忽略
        return [embed_hash(t, dim=self.dim) for t in texts]

    def estimate_cost(self, texts):
        return 0.0, sum(len(t) for t in texts)


# ========== 远程语义嵌入（REST，httpx） ==========

class _RemoteProvider:
    """远程嵌入公共骨架：批次切分、超时、熔断、失败降级哈希。

    子类实现 _call_api(texts) -> list[list[float]]（同步，抛异常表示失败）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._fail_streak = 0          # 连续失败次数
        self._cooldown_until = 0.0     # 熔断冷却截止时间戳
        self._no_key_warned = False

    @property
    def dim(self):
        return SEMANTIC_EMBEDDING_DIM

    @property
    def api_key(self) -> str:
        raise NotImplementedError

    def _call_api(self, texts):
        raise NotImplementedError

    # ---- 熔断 ----

    def _circuit_open(self) -> bool:
        with self._lock:
            return time.time() < self._cooldown_until

    def _record_result(self, ok: bool):
        with self._lock:
            if ok:
                self._fail_streak = 0
            else:
                self._fail_streak += 1
                if self._fail_streak >= EMBED_CIRCUIT_FAILURES:
                    self._cooldown_until = time.time() + EMBED_CIRCUIT_COOLDOWN
                    self._fail_streak = 0
                    logger.warning(
                        "嵌入 API 连续失败 %d 次，熔断 %.0f 秒（期间自动降级哈希嵌入）",
                        EMBED_CIRCUIT_FAILURES, EMBED_CIRCUIT_COOLDOWN,
                    )

    # ---- 统一入口 ----

    def embed(self, texts, strict=False, fallback_dim=None):
        """批量嵌入：自动分批 + 熔断 + 失败降级哈希（不抛异常，约束 3）。

        返回与 texts 等长的向量列表；整体失败时全部回退哈希。fallback_dim 指定
        降级哈希向量的维度（查询侧传 Neo4j 现存索引的实际维度，保证降级后
        向量查询仍可用、等价于升级前哈希路径；缺省对齐语义索引维度）。

        strict=True（建索引批处理用）：任何失败直接抛异常——建库路径若静默降级，
        会把哈希向量以语义模型的名义存进 MySQL 真源，污染数据且事后不可分辨；
        中断后重跑脚本自动续算。
        """
        fdim = fallback_dim or self.dim
        if not texts:
            return []
        if not self.api_key:
            if strict:
                raise RuntimeError(f"{self.name} 未配置 API Key，无法构建语义索引")
            if not self._no_key_warned:
                logger.warning("%s 未配置 API Key，本次及后续嵌入调用降级哈希嵌入", self.name)
                self._no_key_warned = True
            return self._fallback(texts, fdim)
        if self._circuit_open():
            if strict:
                raise RuntimeError(f"{self.name} 嵌入处于熔断冷却期，请稍后重跑")
            return self._fallback(texts, fdim)
        vectors = []
        ok = True
        degraded = False
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i:i + self.batch_size]
            try:
                vectors.extend(self._call_api(chunk))
            except Exception as e:  # noqa: BLE001 任何远程异常都降级，不影响调用方
                if strict:
                    self._record_result(False)
                    raise
                logger.warning("%s 嵌入调用失败（本批 %d 条降级哈希）: %s",
                               self.name, len(chunk), e)
                vectors.extend(self._fallback(chunk, fdim))
                degraded = True
                ok = False
                break
        self._record_result(ok)
        if len(vectors) < len(texts):
            vectors.extend(self._fallback(texts[len(vectors):], fdim))
            degraded = True
        # 维度校验：远程返回维度与预期不一致视为失败降级（防 Neo4j 查询炸）；
        # 已降级批次为哈希兜底向量，维度以 fdim 为准，不参与该校验
        if not degraded:
            bad = [v for v in vectors if len(v) != self.dim]
            if bad:
                logger.warning("%s 返回维度 %d != 预期 %d，整体降级哈希",
                               self.name, len(bad[0]) if bad else 0, self.dim)
                return self._fallback(texts, fdim)
        return vectors

    def _fallback(self, texts, dim=None):
        return [embed_hash(t, dim=dim or self.dim) for t in texts]

    def estimate_cost(self, texts):
        """(预估费用元, 预估 tokens)；token 按中文字符≈1 token 粗估（宁可高估）"""
        tokens = sum(max(1, len(t)) for t in texts)
        cost = tokens / 1000.0 * self.cost_per_1k
        return cost, tokens


class ZhipuProvider(_RemoteProvider):
    """智谱 embedding-3：POST /api/paas/v4/embeddings，批次 64 条"""

    name = "zhipu"
    batch_size = 64
    cost_per_1k = EMBED_COST_PER_1K_ZHIPU
    URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
    MODEL = "embedding-3"

    def __init__(self):
        super().__init__()

    @property
    def model_ver(self):
        return EMBEDDING_MODEL_VER or "zhipu-embedding-3"

    @property
    def api_key(self):
        return ZHIPU_API_KEY

    def _call_api(self, texts):
        resp = httpx.post(
            EMBED_API_BASE_URL or self.URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.MODEL, "input": texts, "dimensions": self.dim},
            timeout=EMBED_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]


class TongyiProvider(_RemoteProvider):
    """通义 text-embedding-v3：DashScope OpenAI 兼容端点，批次 10 条"""

    name = "tongyi"
    batch_size = 10
    cost_per_1k = EMBED_COST_PER_1K_TONGYI
    URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    MODEL = "text-embedding-v3"

    def __init__(self):
        super().__init__()

    @property
    def model_ver(self):
        return EMBEDDING_MODEL_VER or "tongyi-text-embedding-v3"

    @property
    def api_key(self):
        return DASHSCOPE_API_KEY

    def _call_api(self, texts):
        resp = httpx.post(
            EMBED_API_BASE_URL or self.URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.MODEL, "input": texts,
                  "dimensions": self.dim, "encoding_format": "float"},
            timeout=EMBED_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]


class QwenProvider(_RemoteProvider):
    """千问（OpenAI 兼容 embeddings）：专属部署场景，端点与模型名均走 .env。

    - EMBED_API_BASE_URL：完整 embeddings URL（专属网关无公共默认，必须配置）；
    - EMBEDDING_MODEL：模型名（如 qwen3.7-text-embedding）；
    - api_key 复用 DASHSCOPE_API_KEY 项（百炼系密钥格式）。
    批次默认 10（与 text-embedding-v3 一致），可用 EMBED_BATCH_SIZE 调整。
    """

    name = "qwen"
    cost_per_1k = EMBED_COST_PER_1K_QWEN

    def __init__(self):
        super().__init__()
        self.batch_size = int(os.getenv("EMBED_BATCH_SIZE", "10"))

    @property
    def model(self) -> str:
        return EMBEDDING_MODEL or "qwen3.7-text-embedding"

    @property
    def model_ver(self):
        return EMBEDDING_MODEL_VER or f"qwen-{self.model}"

    @property
    def api_key(self):
        return DASHSCOPE_API_KEY

    def _call_api(self, texts):
        if not EMBED_API_BASE_URL:
            raise RuntimeError("qwen provider 需配置 EMBED_API_BASE_URL（专属部署无公共默认端点）")
        resp = httpx.post(
            EMBED_API_BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts,
                  "dimensions": self.dim, "encoding_format": "float"},
            timeout=EMBED_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        # OpenAI 兼容形状 {data:[{index, embedding}]}；部分网关直接返回 {embeddings:[[...]]}
        if "data" in body:
            data = body["data"]
            return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]
        return body["embeddings"]


# ========== 工厂 + 查询嵌入缓存 ==========

_provider = None
_provider_lock = threading.Lock()


def get_provider():
    """按 EMBEDDING_PROVIDER 返回进程级单例；未知/未就绪一律哈希兜底"""
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                if EMBEDDING_PROVIDER == "zhipu":
                    _provider = ZhipuProvider()
                elif EMBEDDING_PROVIDER == "tongyi":
                    _provider = TongyiProvider()
                elif EMBEDDING_PROVIDER == "qwen":
                    _provider = QwenProvider()
                else:
                    _provider = HashProvider()
    return _provider


def semantic_mode() -> bool:
    """当前是否语义嵌入模式（provider=hash 时行为与升级前逐位一致）"""
    return not isinstance(get_provider(), HashProvider)


def active_dim() -> int:
    """当前生效的嵌入维度（Neo4j 向量索引维度必须与之一致）"""
    return get_provider().dim


# 查询嵌入短 TTL 缓存（同句高频复用；只缓存进程内，不污染 MySQL 真源）
_qcache: dict[str, tuple[float, list]] = {}
_qcache_lock = threading.Lock()


async def embed_query_async(text: str, fallback_dim: int = None):
    """异步版查询嵌入：协程环境用（vector_recall），远程调用不阻塞事件循环。

    fallback_dim：降级哈希向量的维度（调用方传 Neo4j 索引实际维度）。
    """
    import asyncio
    cached = _cache_get(text)
    if cached is not None:
        return cached
    provider = get_provider()
    loop = asyncio.get_running_loop()
    vectors = await loop.run_in_executor(
        None, lambda: provider.embed([text], fallback_dim=fallback_dim))
    vec = vectors[0]
    _cache_put(text, vec)
    return vec


def embed_query_sync(text: str) -> list:
    """同步版查询嵌入（脚本/线程内用）"""
    cached = _cache_get(text)
    if cached is not None:
        return cached
    vec = get_provider().embed([text])[0]
    _cache_put(text, vec)
    return vec


def _cache_get(text: str):
    with _qcache_lock:
        hit = _qcache.get(text)
        if hit and hit[0] > time.time():
            return hit[1]
        if hit:
            del _qcache[text]
    return None


def _cache_put(text: str, vec: list):
    with _qcache_lock:
        if len(_qcache) >= 1024:  # 简易容量保护：满了即清（查询嵌入本就短 TTL）
            _qcache.clear()
        _qcache[text] = (time.time() + EMBED_CACHE_TTL, vec)


# ========== float32 BLOB 编解码（MySQL entity_embeddings 存储） ==========


def vec_to_blob(vec) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def blob_to_vec(blob: bytes, dim: int) -> list:
    return list(struct.unpack(f"<{dim}f", blob))


def entity_key_hash(label: str, name: str) -> str:
    """entity_embeddings 主键之一：sha256(label \\x1f name)，规避 utf8mb4 长索引"""
    return hashlib.sha256(f"{label}\x1f{name}".encode("utf-8")).hexdigest()
