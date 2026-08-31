# coding: utf-8
"""
契约测试基座（阶段五）：先于后端分层重构落地，锁定全部 /api 路由的
「路径 + 参数 + 响应字段形状」基线；重构后跑本套测试即可发现任何对外契约漂移。

设计要点：
- 结构签名（sig）只记录键名与类型、不记录具体值，故图谱数据/计数/时间戳变化
  不会误报，只有字段增删改名（真正破坏契约）才触发不匹配；
- Neo4j 不可用时自动 skip（避免无库环境误判）；
- 认证依赖用 dependency_overrides 注入测试用户，只读接口无需真实令牌；
- 快照录制：设环境变量 CONTRACT_RECORD=1 重新生成基线，否则只读比对。
"""

import os
import sys
import json

import pytest
from fastapi.testclient import TestClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import app as app_module  # noqa: E402

SNAP_DIR = os.path.join(_BACKEND_DIR, "tests", "contract_snapshots")
os.makedirs(SNAP_DIR, exist_ok=True)


def _neo4j_available() -> bool:
    try:
        from services.graph_db import run_cypher_sync
        run_cypher_sync("RETURN 1 AS x", {})
        return True
    except Exception:
        return False


def sig(obj):
    """把响应体转为结构签名：dict→{键: 递归签名}，list→[首元素签名]，标量→类型名。

    只保留形状与字段名，丢弃具体值，从而对数据内容变化免疫、只对字段漂移敏感。
    """
    if isinstance(obj, dict):
        return {k: sig(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sig(obj[0])] if obj else []
    return type(obj).__name__


def check_snapshot(name: str, actual):
    """比对（或录制）命名快照。首次运行或 CONTRACT_RECORD=1 时写入基线。"""
    path = os.path.join(SNAP_DIR, f"{name}.json")
    recording = os.environ.get("CONTRACT_RECORD") == "1"
    if recording or not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(actual, f, ensure_ascii=False, indent=2, sort_keys=True)
        return
    with open(path, "r", encoding="utf-8") as f:
        expected = json.load(f)
    assert expected == actual, (
        f"契约漂移 [{name}]:\n"
        f"  基线: {json.dumps(expected, ensure_ascii=False, sort_keys=True)}\n"
        f"  实际: {json.dumps(actual, ensure_ascii=False, sort_keys=True)}"
    )


@pytest.fixture(scope="session")
def client():
    """共享 TestClient；触发应用 lifespan（连库、建索引、预加载词典）。"""
    if not _neo4j_available():
        pytest.skip("Neo4j 不可用，跳过契约测试")
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    """在 client 基础上覆盖认证依赖，注入固定测试用户（仅用于读接口的响应形状测试）。"""
    app_module.app.dependency_overrides[app_module.get_current_user] = lambda: "ci_test_user"
    app_module.app.dependency_overrides[app_module.optional_user] = lambda: "ci_test_user"
    yield client
    app_module.app.dependency_overrides.clear()
