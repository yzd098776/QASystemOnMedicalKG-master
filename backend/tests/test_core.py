# coding: utf-8
"""
关键逻辑回归测试（阶段五）：优先覆盖 HANDOFF 指定的四类高风险路径——
疾病自查匹配、药物相互作用、Cypher 注入防护、认证流程；另含 Text2Cypher 写拒绝。
"""

import asyncio
import time
from urllib.parse import quote

import pytest

import app as A
from services.diagnosis_service import run_diagnosis
from services.drug_service import run_drug_interaction
from services.text2cypher import validate_cypher


# ========== 1. 疾病自查加权匹配 ==========

def test_diagnosis_weighted_structure(client):
    res = asyncio.run(run_diagnosis(["发烧", "咳嗽"]))
    assert "results" in res
    rs = res["results"]
    if rs:
        # matchedCount 不得超过输入症状数（阶段二越界修复）
        assert all(r["matchedCount"] <= 2 for r in rs), "matchedCount 越界"
        # 结果按相对置信度降序
        probs = [r["probability"] for r in rs]
        assert probs == sorted(probs, reverse=True), "结果未按匹配度降序"
        # 契约字段齐全（含阶段二新增 match_evidence / prior）
        assert {"name", "matchedSymptoms", "matchedCount", "probability",
                "department", "match_evidence", "prior"}.issubset(rs[0])


def test_diagnosis_alias_normalization(client):
    """口语症状词经别名归一化应能召回规范名关联疾病：发烧类同义不应报错。"""
    res = asyncio.run(run_diagnosis(["发烧"]))
    assert "results" in res and isinstance(res["results"], list)


# ========== 2. 药物相互作用 ==========

def test_drug_interaction_structure(client):
    res = asyncio.run(run_drug_interaction(["阿莫西林", "头孢克肟"]))
    assert "interactions" in res
    for it in res["interactions"]:
        assert {"drug1", "drug2", "risk", "description"}.issubset(it)
    # 两药 -> 恰好一个两两组合
    assert len(res["interactions"]) == 1


# ========== 3. Cypher 注入防护回归 ==========

@pytest.mark.parametrize("payload", [
    "'; MATCH (n) DETACH DELETE n RETURN n //",
    "mao) OR (1=1",
    "{} ] } { $param: 1",
    "1' OR '1'='1",
])
def test_cypher_injection_parameterized(client, payload):
    """恶意字符串经参数化传入被当作字面量：不得 500，最多返回空结果。"""
    r = client.get("/api/kg/entities", params={"search": payload, "limit": 5})
    assert r.status_code in (200, 400), "注入输入不应导致 500"
    if r.status_code == 200:
        assert isinstance(r.json()["nodes"], list)


@pytest.mark.parametrize("path_param", [
    "'; DROP DATABASE neo4j //",
    "$name",
    "x) RETURN 1 //",
])
def test_entity_detail_injection_safe(client, path_param):
    r = client.get("/api/kg/entity/" + quote(path_param))
    assert r.status_code in (200, 400, 404), "路径参数注入不得 500"


def test_illegal_label_returns_empty(client):
    """非白名单标签不得拼入 Cypher，应返回空结果而非报错（保持既有兼容行为）。"""
    r = client.get("/api/kg/entities", params={"type": "NotALabel", "limit": 5})
    assert r.status_code == 200
    assert r.json()["nodes"] == []


# ========== 4. Text2Cypher 写拒绝（阶段三安全边界） ==========

@pytest.mark.parametrize("bad", [
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) RETURN n; CREATE (x:Evil)",
    "CALL { MATCH (n) CREATE (m) } RETURN 1",
    "MATCH (n) RETURN n UNION MATCH (m) SET m.pwned = 1",
    "MATCH (n) MERGE (n)-[:X]->(m)",
])
def test_text2cypher_rejects_writes(bad):
    ok, _ = validate_cypher(bad)
    assert not ok, "恶意 Cypher 未被拒绝: " + bad


def test_text2cypher_allows_readonly_and_bounds():
    ok, res = validate_cypher("MATCH (n:Disease) RETURN n.name")
    assert ok and "LIMIT" in res  # 无 LIMIT 自动补


# ========== 5. 认证流程端到端（真实令牌，不落盘） ==========

def test_auth_flow(client, monkeypatch):
    """注册 -> 登录 -> 真实令牌访问受保护接口 -> 错误密码 401 -> 登出后令牌失效。

    覆盖阶段一：双令牌、jti/token_version 失效、密码哈希、401 拒绝。
    用 monkeypatch 屏蔽落盘，测毕清理内存态，不污染 users.json。
    """
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(A, "save_json_async", _noop)
    monkeypatch.setattr(A, "save_json", lambda *a, **k: None)

    user = "citest" + str(int(time.time()))[-8:]  # 控制在 20 字符内（RegisterRequest 上限）
    pwd = "Str0ngPass!2026"
    try:
        r = client.post("/api/auth/register",
                        json={"username": user, "email": user + "@ci.local", "password": pwd})
        assert r.status_code in (200, 201), r.text

        r = client.post("/api/auth/login", json={"username": user, "password": pwd})
        assert r.status_code == 200, r.text
        body = r.json()
        # 阶段一登录响应契约：保留原 token/user 字段，追加 refresh_token / expires_in
        assert {"token", "user", "refresh_token", "expires_in"}.issubset(body)
        tok = body["token"]

        # 真实令牌走完整校验链
        r = client.get("/api/profile/get", headers={"Authorization": "Bearer " + tok})
        assert r.status_code == 200, r.text

        # 错误密码 -> 401
        r = client.post("/api/auth/login", json={"username": user, "password": "wrong-password-1"})
        assert r.status_code == 401

        # 刷新令牌换取新访问令牌
        r = client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 200
        assert "token" in r.json() and "refresh_token" in r.json()

        # 登出 -> 该 access 令牌随即失效
        r = client.post("/api/auth/logout", headers={"Authorization": "Bearer " + tok})
        assert r.status_code == 200
        r = client.get("/api/profile/get", headers={"Authorization": "Bearer " + tok})
        assert r.status_code == 401
    finally:
        A.users_db.pop(user, None)
        A.profiles_db.pop(user, None)


def test_protected_route_rejects_garbage_token(client):
    r = client.get("/api/profile/get", headers={"Authorization": "Bearer garbage.token.value"})
    assert r.status_code == 401
