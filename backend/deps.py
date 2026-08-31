# coding: utf-8
"""
共享依赖与工具层（阶段五分层重构）：JWT 认证依赖、实体输入校验、别名归一化候选、
跨标签定位 Cypher 片段生成、缓存薄封装。自 app.py 抽出，行为逐字一致。

路由层通过 `from deps import get_current_user, optional_user, _validate_entity_input, ...`
引用；认证依赖为同一函数对象，供测试 dependency_overrides 精确匹配。
"""

from typing import Optional

from fastapi import HTTPException, Request

from core.security import decode_token, validate_access_payload
from core.alias import normalize as normalize_alias
from core.cache import get_cache
from store import users_db

# 模块级实体标签白名单：Neo4j 节点标签不可参数化，只有该白名单内的标签才允许拼接进 Cypher；
# 该白名单同时供 Text2Cypher 的白名单校验复用
ALLOWED_LABELS = {"Disease", "Drug", "Symptom", "Food", "Check", "Department", "Producer"}
# 标签遍历固定顺序（字母序）：保证生成 Cypher 稳定，便于缓存查询计划与日志排查
_LABEL_SCAN_ORDER = tuple(sorted(ALLOWED_LABELS))


# ========== 认证依赖 ==========
def _extract_bearer(request: Request) -> str:
    """从 Authorization 头提取 Bearer 令牌，缺失时 401"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    return auth[7:]


def get_current_user(request: Request) -> str:
    # 校验链：签名/有效期 -> 用户存在 -> type=access -> jti 黑名单 -> token_version 一致性
    payload = decode_token(_extract_bearer(request))
    return validate_access_payload(payload, users_db)


def _current_payload(request: Request) -> dict:
    """解码并完整校验 Bearer 令牌，返回载荷（供登出等需要 jti/exp 的接口使用）"""
    payload = decode_token(_extract_bearer(request))
    validate_access_payload(payload, users_db)
    return payload


def optional_user(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = decode_token(auth[7:])
        # 同样走黑名单与 token_version 校验，失效令牌视为匿名访问
        return validate_access_payload(payload, users_db)
    except HTTPException:
        return None


# ========== 输入校验与图谱定位工具 ==========
def _validate_entity_input(value, name="参数"):
    """轻量输入校验助手：
    - 空值/纯空白返回 None，由调用方按各自契约返回空结果（保持原有“未命中返回空结构”行为）
    - 长度超过 200 时返回 400，拦截明显异常输入
    所有 Cypher 查询均以参数化方式传值，本身无注入风险，无需再做关键词黑名单过滤。
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 200:
        raise HTTPException(status_code=400, detail=f"{name}过长，请控制在200字符以内")
    return value


def _alias_candidates(value):
    """构造别名归一化后的查询候选序列：归一化名优先，原词兜底。
    各接入点按序尝试，命中即返回；全部未命中时由调用方按各自既有契约返回空结构。
    传入 None 时返回 [None]（表示不带关键词查询）。
    """
    if value is None:
        return [None]
    normalized = normalize_alias(value)
    if normalized and normalized != value:
        return [normalized, value]
    return [value]


def _name_match_union(var: str, param: str) -> str:
    """生成「按 name 跨七类实体标签查节点」的 CALL 子查询片段（含 CALL 包裹）。

    背景（阶段三索引收益落地）：无标签 `MATCH (n {name:...})` 无法命中任何标签级
    唯一约束/索引，PROFILE 实测为 AllNodesScan 全表扫描（~4.4 万 dbHits）。
    曾尝试方案一：建 token-less 全局索引 `CREATE INDEX IF NOT EXISTS FOR (n) ON (n.name)`，
    实测本库 Neo4j 5.26 不支持无标签属性索引语法（服务端 SyntaxError）；
    故采用方案二：按 ALLOWED_LABELS 拆成逐标签分支（UNION ALL），每个分支带标签后
    命中对应标签的约束/索引（NodeIndexSeek，约 2 dbHits/分支），整体降至两位数。
    标签仅来自 ALLOWED_LABELS 白名单，name 值仍以 $参数传入，无注入风险。
    CALL 使用空变量作用域子句 `CALL () { ... }`（Neo4j 5 推荐写法，避免弃用告警；
    子查询只需 $参数、不导入外部变量，参数在子查询内天然可见）。
    """
    branches = [
        "MATCH (%s:%s {name: $%s}) RETURN %s" % (var, label, param, var)
        for label in _LABEL_SCAN_ORDER
    ]
    return "CALL () {\n" + "\nUNION ALL\n".join(branches) + "\n}"


# ========== 缓存薄封装（兼容既有调用签名） ==========
def _cache_get(key: str, ttl: int = 300):
    return get_cache().get(key)


def _cache_set(key: str, value, ttl: int = None):
    get_cache().set(key, value, ttl=ttl)
