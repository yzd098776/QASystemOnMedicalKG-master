# coding: utf-8
"""
API 契约测试（阶段五）：对每个 /api 路由录制「状态码 + 响应字段形状」基线，
后端分层重构后运行本文件即可捕获任何对外契约漂移（路径/参数/字段改名或删除）。

运行：
    录制基线：CONTRACT_RECORD=1 python -m pytest tests/test_contract.py
    比对校验：python -m pytest tests/test_contract.py
"""

from conftest import sig, check_snapshot

# 说明：快照值统一为 {"status": <int>, "body": <形状签名>}，只锁形状与状态码不锁业务数据值。


def _snap(client, name, method, path, *, auth=False, **kw):
    c = client
    resp = getattr(c, method)(path, **kw)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    check_snapshot(name, {"status": resp.status_code, "body": sig(body)})
    return resp


# ========== 公开只读接口 ==========

def test_health(client):
    _snap(client, "health", "get", "/health")


def test_kg_entities_browse(client):
    _snap(client, "kg_entities_browse", "get", "/api/kg/entities", params={"limit": 3})


def test_kg_entities_search_with_cursor(client):
    # 阶段四游标：响应须含 next_cursor 字段
    _snap(client, "kg_entities_search", "get", "/api/kg/entities", params={"search": "感冒", "limit": 3})


def test_kg_entity_detail(client):
    _snap(client, "kg_entity_detail", "get", "/api/kg/entity/高血压")


def test_kg_path(client):
    _snap(client, "kg_path", "get", "/api/kg/path", params={"source": "感冒", "target": "发烧"})


def test_kg_related(client):
    _snap(client, "kg_related", "get", "/api/kg/related", params={"entity": "感冒", "depth": 1})


def test_kg_neighbors(client):
    _snap(client, "kg_neighbors", "get", "/api/kg/neighbors", params={"name": "感冒", "depth": 1, "limit": 20})


def test_diagnosis(client):
    _snap(client, "diagnosis", "post", "/api/diagnosis", json={"symptoms": ["发烧", "咳嗽"]})


def test_drug_contraindication(client):
    _snap(client, "drug_contraindication", "get", "/api/drug/contraindication", params={"drug": "阿莫西林"})


def test_food_contraindication(client):
    _snap(client, "food_contraindication", "get", "/api/food/contraindication", params={"query": "橙子", "type": "food"})


def test_drug_interaction(client):
    _snap(client, "drug_interaction", "post", "/api/drug/interaction", json={"drugs": ["阿莫西林", "头孢"]})


def test_guide_department(client):
    _snap(client, "guide_department", "get", "/api/guide/department", params={"query": "感冒"})


def test_guide_check(client):
    _snap(client, "guide_check", "get", "/api/guide/check", params={"query": "发烧"})


def test_wiki_daily_tip(client):
    _snap(client, "wiki_daily_tip", "get", "/api/wiki/daily-tip")


# ========== 需认证只读接口（注入测试用户） ==========

def test_profile_get(auth_client):
    _snap(auth_client, "profile_get", "get", "/api/profile/get", auth=True)


def test_user_export(auth_client):
    _snap(auth_client, "user_export", "get", "/api/user/export", auth=True)


def test_health_records_get(auth_client):
    _snap(auth_client, "health_records_get", "get", "/api/health/records", auth=True)


def test_health_plans_get(auth_client):
    _snap(auth_client, "health_plans_get", "get", "/api/health/plans", auth=True)


def test_chat_history_get(auth_client):
    _snap(auth_client, "chat_history_get", "get", "/api/chat/history", auth=True)


# ========== 写接口：未认证应被拒绝（证明路由存在且受保护） ==========

def test_profile_update_requires_auth(client):
    r = client.post("/api/profile/update", json={"age": 30})
    check_snapshot("profile_update_unauth", {"status": r.status_code, "body": sig(r.json())})
    assert r.status_code == 401


def test_health_record_save_requires_auth(client):
    r = client.post("/api/health/records", json={"date": "2026-01-01"})
    assert r.status_code == 401


def test_health_plan_save_requires_auth(client):
    r = client.post("/api/health/plans", json={"type": "prevention", "data": {}, "disease": "高血压"})
    assert r.status_code == 401


def test_chat_history_save_requires_auth(client):
    r = client.post("/api/chat/history/save", json={"session_id": "s1", "messages": []})
    assert r.status_code == 401


def test_chat_history_clear_requires_auth(client):
    r = client.delete("/api/chat/history")
    assert r.status_code == 401


def test_health_record_delete_requires_auth(client):
    r = client.delete("/api/health/records/xxx")
    assert r.status_code == 401


def test_health_plans_clear_requires_auth(client):
    r = client.delete("/api/health/plans")
    assert r.status_code == 401


def test_user_data_delete_requires_auth(client):
    r = client.delete("/api/user/data")
    assert r.status_code == 401


def test_logout_requires_auth(client):
    r = client.post("/api/auth/logout")
    assert r.status_code == 401


# ========== 认证类接口：参数校验负例（不触发写盘副作用） ==========

def test_register_validation_422(client):
    # 用户名过短 + 密码过短 → Pydantic 422，不进入写库逻辑
    r = client.post("/api/auth/register", json={"username": "ab", "email": "x@y.z", "password": "123"})
    check_snapshot("register_validation", {"status": r.status_code, "body": sig(r.json())})
    assert r.status_code == 422


def test_login_wrong_password_401(client):
    r = client.post("/api/auth/login", json={"username": "no_such_user_ci", "password": "wrongpass123"})
    check_snapshot("login_wrong", {"status": r.status_code, "body": sig(r.json())})
    assert r.status_code == 401


def test_refresh_invalid_token_401(client):
    r = client.post("/api/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 401


# ========== 参数校验 / 输入防护（阶段一回归） ==========

def test_entities_too_long_400(client):
    r = client.get("/api/kg/entities", params={"search": "感冒" * 101})
    check_snapshot("entities_too_long", {"status": r.status_code})
    assert r.status_code == 400


def test_diagnosis_empty_body_422(client):
    r = client.post("/api/diagnosis", json={})
    assert r.status_code == 422


def test_diagnosis_no_symptoms_400(client):
    r = client.post("/api/diagnosis", json={"symptoms": [""]})
    assert r.status_code == 400


def test_entity_404(client):
    r = client.get("/api/kg/entity/肯定不存在的实体xyz123")
    check_snapshot("entity_404", {"status": r.status_code, "body": sig(r.json())})
    assert r.status_code == 404


# ========== /api/chat SSE 帧协议契约 ==========

def test_chat_sse_emergency_frames(client):
    """急症问题：SSE 流须以 emergency 帧起始且以 [DONE] 结束，且不产生检索/LLM 依赖。"""
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "我突然胸痛倒地"}]})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    assert '"emergency"' in r.text
    assert r.text.strip().endswith("data: [DONE]")


def test_chat_sse_graph_lookup_frames(client):
    """百科问题：intent 帧为 graph_lookup，含 content 帧与 sources 帧，结束 [DONE]。"""
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "什么是高血压"}]})
    assert '"intent"' in r.text and '"graph_lookup"' in r.text
    assert '"sources"' in r.text
    assert r.text.strip().endswith("data: [DONE]")
