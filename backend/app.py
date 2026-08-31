"""
医疗知识图谱智能问答系统 - FastAPI后端
基于刘焕勇的医疗知识图谱项目扩展
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()
import time
import hashlib
import asyncio
import logging
import secrets
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, EmailStr
from jose import JWTError, jwt
import bcrypt
import httpx
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== 配置 ==========
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")
SECRET_KEY = os.getenv("JWT_SECRET") or secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

if not DEEPSEEK_API_KEY:
    logger.warning("DEEPSEEK_API_KEY 未设置，AI问答将使用模拟响应")
if not NEO4J_PASSWORD:
    logger.warning("NEO4J_PASSWORD 未设置，将使用默认密码")
    NEO4J_PASSWORD = "12345678"

# ========== 密码与JWT ==========


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))


def create_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的认证令牌")


# ========== Neo4j 连接 ==========
driver = None


def get_driver():
    global driver
    if driver is None:
        driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
            max_connection_pool_size=50,
            connection_acquisition_timeout=30,
        )
    return driver


def _run_cypher_sync(query: str, parameters: dict = None):
    d = get_driver()
    with d.session() as session:
        result = session.run(query, parameters or {})
        return [dict(record) for record in result]


async def run_cypher(query: str, parameters: dict = None):
    return await asyncio.to_thread(_run_cypher_sync, query, parameters)


# ========== 简单缓存 ==========
_cache = {}
_cache_ttl = {}


def _cache_get(key: str, ttl: int = 300):
    if key in _cache and time.time() - _cache_ttl.get(key, 0) < ttl:
        return _cache[key]
    return None


def _cache_set(key: str, value):
    _cache[key] = value
    _cache_ttl[key] = time.time()


# ========== 用户存储（内存 + JSON文件持久化） ==========
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")
PROFILES_FILE = os.path.join(os.path.dirname(__file__), "profiles.json")
HEALTH_RECORDS_FILE = os.path.join(os.path.dirname(__file__), "health_records.json")
HEALTH_PLANS_FILE = os.path.join(os.path.dirname(__file__), "health_plans.json")
CHAT_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "chat_history.json")


def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载 {path} 失败: {e}")
            return {}
    return {}


def save_json(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


users_db = load_json(USERS_FILE)
profiles_db = load_json(PROFILES_FILE)
health_records_db = load_json(HEALTH_RECORDS_FILE)
health_plans_db = load_json(HEALTH_PLANS_FILE)
chat_history_db = load_json(CHAT_HISTORY_FILE)
chat_sessions = {}


# ========== Pydantic 模型 ==========
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    email: str
    password: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    age: Optional[int] = None
    gender: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    blood_type: Optional[str] = None
    allergy_drug: Optional[str] = None
    allergy_food: Optional[str] = None
    medical_history: Optional[str] = None
    family_history: Optional[str] = None
    smoking: Optional[bool] = False
    drinking: Optional[bool] = False


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    context: Optional[str] = None


class DiagnosisRequest(BaseModel):
    symptoms: List[str]


class DrugInteractionRequest(BaseModel):
    drugs: List[str]


class HealthRecord(BaseModel):
    date: str
    weight: Optional[float] = None
    bloodPressureHigh: Optional[int] = None
    bloodPressureLow: Optional[int] = None
    bloodSugar: Optional[float] = None
    heartRate: Optional[int] = None
    note: Optional[str] = None


# ========== 认证中间件 ==========
def get_current_user(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    token = auth[7:]
    payload = decode_token(token)
    username = payload.get("sub")
    if not username or username not in users_db:
        raise HTTPException(status_code=401, detail="用户不存在")
    return username


def optional_user(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        token = auth[7:]
        payload = decode_token(token)
        return payload.get("sub")
    except Exception:
        return None


# ========== FastAPI 应用 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时测试Neo4j连接
    try:
        await run_cypher("RETURN 1 as test")
        print("[OK] Neo4j connected successfully")
    except Exception as e:
        print(f"[FAIL] Neo4j connection failed: {e}")
    yield
    # 关闭时清理
    if driver:
        driver.close()


app = FastAPI(
    title="医疗知识图谱智能问答系统 API",
    description="基于4.4万实体+30万关系的医疗知识图谱后端接口",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 用户认证接口 ==========
@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    if req.username in users_db:
        raise HTTPException(status_code=400, detail="用户名已存在")
    for u in users_db.values():
        if u.get("email") == req.email:
            raise HTTPException(status_code=400, detail="邮箱已被注册")
    users_db[req.username] = {
        "username": req.username,
        "email": req.email,
        "password": hash_password(req.password),
        "created_at": datetime.now().isoformat(),
    }
    save_json(USERS_FILE, users_db)
    return {"message": "注册成功"}


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    user = users_db.get(req.username)
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token({"sub": req.username})
    return {
        "token": token,
        "user": {"username": req.username, "email": user["email"]},
    }


# ========== 健康档案接口 ==========
@app.get("/api/profile/get")
async def get_profile(username: str = Depends(get_current_user)):
    return profiles_db.get(username, {})


@app.post("/api/profile/update")
async def update_profile(profile: ProfileUpdate, username: str = Depends(get_current_user)):
    profiles_db[username] = profile.model_dump(exclude_none=True)
    save_json(PROFILES_FILE, profiles_db)
    return {"message": "健康档案已更新"}


# ========== 知识图谱接口 ==========
def sanitize_input(text: str) -> str:
    """防止Cypher注入"""
    if not text:
        return ""
    # 移除可能的注入字符
    forbidden = [";", "//", "/*", "*/", "DROP", "DELETE", "REMOVE", "DETACH"]
    result = text
    for f in forbidden:
        result = result.replace(f, "")
    return result.strip()


@app.get("/api/kg/entities")
async def search_entities(
    search: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    page: int = Query(default=1, ge=1),
):
    safe_search = sanitize_input(search) if search else None
    safe_type = sanitize_input(type) if type else None

    skip = (page - 1) * limit

    if safe_search and safe_type:
        query = """
        MATCH (n)
        WHERE n.name CONTAINS $search AND $type IN labels(n)
        RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props
        SKIP $skip LIMIT $limit
        """
        params = {"search": safe_search, "type": safe_type, "skip": skip, "limit": limit}
    elif safe_search:
        query = """
        MATCH (n)
        WHERE n.name CONTAINS $search
        RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props
        SKIP $skip LIMIT $limit
        """
        params = {"search": safe_search, "skip": skip, "limit": limit}
    elif safe_type:
        # Neo4j 不支持参数化标签，使用白名单校验
        allowed_labels = {"Disease", "Drug", "Symptom", "Food", "Check", "Department", "Producer"}
        if safe_type not in allowed_labels:
            return {"nodes": [], "links": [], "total": 0}
        query = f"""
        MATCH (n:{safe_type})
        RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props
        SKIP $skip LIMIT $limit
        """
        params = {"skip": skip, "limit": limit}
    else:
        query = """
        MATCH (n)
        RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props
        SKIP $skip LIMIT $limit
        """
        params = {"skip": skip, "limit": limit}

    try:
        results = await run_cypher(query, params)
    except Exception as e:
        logger.error(f"实体搜索失败: {e}")
        return {"nodes": [], "links": [], "total": 0}

    nodes = []
    for r in results:
        nodes.append({
            "name": r["name"],
            "label": r["label"],
            "desc": r["props"].get("desc", ""),
        })

    # 获取关联边
    if len(nodes) > 0:
        names = [n["name"] for n in nodes[:50]]  # 限制边查询数量
        link_query = """
        MATCH (a)-[r]->(b)
        WHERE a.name IN $names AND b.name IN $names
        RETURN a.name AS source, b.name AS target, type(r) AS relType
        LIMIT 200
        """
        try:
            link_results = await run_cypher(link_query, {"names": names})
            links = [{"source": l["source"], "target": l["target"], "relType": l["relType"]} for l in link_results]
        except Exception as e:
            logger.error(f"关联边查询失败: {e}")
            links = []
    else:
        links = []

    # 获取总数（缓存5分钟）
    cached_total = _cache_get("kg_total_count", ttl=300)
    if cached_total is not None:
        total = cached_total
    else:
        count_query = "MATCH (n) RETURN count(n) AS total"
        try:
            count_result = await run_cypher(count_query)
            total = count_result[0]["total"] if count_result else 0
            _cache_set("kg_total_count", total)
        except Exception as e:
            logger.error(f"总数查询失败: {e}")
            total = len(nodes)

    return {"nodes": nodes, "links": links, "total": total}


@app.get("/api/kg/entity/{name}")
async def get_entity_detail(name: str):
    safe_name = sanitize_input(name)
    query = """
    MATCH (n {name: $name})
    OPTIONAL MATCH (n)-[:has_symptom]->(s:Symptom)
    OPTIONAL MATCH (n)-[:common_drug]->(dr:Drug)
    OPTIONAL MATCH (n)-[:do_eat]->(f:Food)
    OPTIONAL MATCH (n)-[:need_check]->(c:Check)
    OPTIONAL MATCH (s2:Symptom)<-[:has_symptom]-(d:Disease)
    WHERE s2.name = n.name
    RETURN n, labels(n)[0] AS label,
      collect(DISTINCT s.name) AS symptoms,
      collect(DISTINCT dr.name) AS drugs,
      collect(DISTINCT f.name) AS foods,
      collect(DISTINCT c.name) AS checks,
      collect(DISTINCT d.name) AS diseases
    """
    try:
        results = await run_cypher(query, {"name": safe_name})
    except Exception as e:
        logger.error(f"实体详情查询失败: {e}")
        raise HTTPException(status_code=500, detail="查询失败")

    if not results:
        raise HTTPException(status_code=404, detail="实体不存在")

    r = results[0]
    node = r["n"]
    label = r["label"]
    props = dict(node) if hasattr(node, 'items') else {}

    entity = {
        "name": safe_name,
        "label": label,
        "properties": props,
    }

    if label == "Disease":
        entity["symptoms"] = r["symptoms"] or []
        entity["drugs"] = r["drugs"] or []
        entity["foods"] = r["foods"] or []
        entity["checks"] = r["checks"] or []
    elif label == "Symptom":
        entity["diseases"] = r["diseases"] or []

    return entity


@app.get("/api/kg/path")
async def find_path(source: str, target: str, max_depth: int = Query(default=5, le=10)):
    safe_source = sanitize_input(source)
    safe_target = sanitize_input(target)

    query = """
    MATCH path = shortestPath(
        (a {name: $source})-[*..""" + str(max_depth) + """]->(b {name: $target})
    )
    RETURN [n IN nodes(path) | n.name] AS nodeNames,
           [r IN relationships(path) | type(r)] AS relTypes
    LIMIT 5
    """
    try:
        results = await run_cypher(query, {"source": safe_source, "target": safe_target})
    except Exception as e:
        logger.error(f"路径查询失败: {e}")
        return {"paths": []}

    paths = []
    for r in results:
        nodes = r["nodeNames"]
        edges = r["relTypes"]
        description_parts = []
        for i in range(len(edges)):
            description_parts.append(f"{nodes[i]} → {edges[i]} → {nodes[i+1]}")
        paths.append({
            "nodes": nodes,
            "edges": edges,
            "description": " → ".join(description_parts) if description_parts else "直接关联",
        })

    return {"paths": paths}


@app.get("/api/kg/related")
async def get_related_entities(entity: str, depth: int = Query(default=1, le=3)):
    safe_entity = sanitize_input(entity)

    if depth == 1:
        query = """
        MATCH (n {name: $entity})-[r]-(m)
        RETURN DISTINCT m.name AS name, labels(m)[0] AS label
        LIMIT 80
        """
    else:
        query = """
        MATCH (n {name: $entity})-[*1..""" + str(depth) + """]-(m)
        RETURN DISTINCT m.name AS name, labels(m)[0] AS label
        LIMIT 200
        """

    try:
        results = await run_cypher(query, {"entity": safe_entity})
    except Exception as e:
        logger.error(f"关联实体查询失败: {e}")
        return {"nodes": [], "links": []}

    # 去重
    seen = set()
    nodes = []
    for r in results:
        if r["name"] not in seen:
            seen.add(r["name"])
            nodes.append({"name": r["name"], "label": r["label"]})

    # 获取根节点的实际标签
    root_query = "MATCH (n {name: $entity}) RETURN labels(n)[0] AS label"
    try:
        root_result = await run_cypher(root_query, {"entity": safe_entity})
        root_label = root_result[0]["label"] if root_result else "Disease"
    except Exception:
        root_label = "Disease"
    nodes.insert(0, {"name": safe_entity, "label": root_label})

    links = []
    if len(nodes) > 1:
        names = [n["name"] for n in nodes[:50]]
        link_query = """
        MATCH (a)-[r]->(b)
        WHERE a.name IN $names AND b.name IN $names
        RETURN a.name AS source, b.name AS target, type(r) AS relType
        LIMIT 200
        """
        try:
            link_results = await run_cypher(link_query, {"names": names})
            links = [{"source": l["source"], "target": l["target"], "relType": l["relType"]} for l in link_results]
        except Exception:
            pass

    return {"nodes": nodes, "links": links}


# ========== 疾病自查接口 ==========
@app.post("/api/diagnosis")
async def diagnosis(req: DiagnosisRequest):
    symptoms = [sanitize_input(s) for s in req.symptoms if s.strip()]
    if not symptoms:
        raise HTTPException(status_code=400, detail="请至少提供一个症状")

    # 查询所有包含这些症状的疾病及其症状、科室、检查（单次查询）
    query = """
    MATCH (d:Disease)-[:has_symptom]->(s:Symptom)
    WHERE s.name IN $symptoms
    WITH d, collect(s.name) AS matchedSymptoms, count(s) AS matchCount
    ORDER BY matchCount DESC
    LIMIT 20
    OPTIONAL MATCH (d)-[:need_check]->(c:Check)
    RETURN d.name AS name, d.desc AS desc, d.cure_department AS department,
      matchedSymptoms, matchCount, collect(c.name) AS checks
    """
    try:
        results = await run_cypher(query, {"symptoms": symptoms})
    except Exception as e:
        logger.error(f"诊断查询失败: {e}")
        return {"results": []}

    total_input = len(symptoms)
    diagnosis_results = []
    for r in results:
        matched = r["matchedSymptoms"]
        count = r["matchCount"]
        probability = round((count / total_input) * 100) if total_input > 0 else 0

        diagnosis_results.append({
            "name": r["name"],
            "desc": r["desc"] or "暂无简介",
            "matchedSymptoms": matched,
            "matchedCount": count,
            "probability": probability,
            "department": r["department"] or "暂无",
            "checks": (r["checks"] or [])[:5],
        })

    return {"results": diagnosis_results}


# ========== 用药安全接口 ==========
@app.get("/api/drug/contraindication")
async def drug_contraindication(drug: str):
    safe_drug = sanitize_input(drug)

    # 查询药品信息
    query = """
    MATCH (dr:Drug {name: $name})
    RETURN dr.name AS name
    """
    try:
        result = await run_cypher(query, {"name": safe_drug})
    except Exception as e:
        logger.error(f"药品查询失败: {e}")
        raise HTTPException(status_code=500, detail="查询失败")

    if not result:
        # 尝试模糊搜索
        fuzzy_q = """
        MATCH (dr:Drug)
        WHERE dr.name CONTAINS $name
        RETURN dr.name AS name
        LIMIT 1
        """
        try:
            result = await run_cypher(fuzzy_q, {"name": safe_drug})
        except Exception:
            pass

    if not result:
        raise HTTPException(status_code=404, detail="未找到该药品")

    drug_name = result[0]["name"]

    # 合并查询：主治疾病、忌吃食物、生产厂商
    combined_q = """
    MATCH (dr:Drug {name: $name})
    OPTIONAL MATCH (dr)<-[:common_drug]-(d:Disease)
    OPTIONAL MATCH (d)-[:no_eat]->(f:Food)
    OPTIONAL MATCH (p:Producer)-[:drugs_of]->(dr)
    RETURN collect(DISTINCT d.name) AS diseases,
      collect(DISTINCT f.name) AS foods,
      collect(DISTINCT p.name) AS producers
    """
    try:
        cr = await run_cypher(combined_q, {"name": drug_name})
        if cr:
            r = cr[0]
            info = {
                "name": drug_name,
                "disease": "、".join(r["diseases"][:5]) if r["diseases"] else "暂无",
                "noEat": (r["foods"] or [])[:10],
                "producer": "、".join(r["producers"][:3]) if r["producers"] else "暂无",
                "contra": [],
            }
        else:
            info = {"name": drug_name, "disease": "暂无", "noEat": [], "producer": "暂无", "contra": []}
    except Exception as e:
        logger.error(f"药品详情查询失败: {e}")
        info = {"name": drug_name, "disease": "暂无", "noEat": [], "producer": "暂无", "contra": []}

    return info


@app.get("/api/food/contraindication")
async def food_contraindication(query: str, type: str = "food"):
    safe_query = sanitize_input(query)

    if type == "food":
        # 按食物查询
        q = """
        MATCH (f:Food {name: $name})
        RETURN f.name AS name
        """
        try:
            result = await run_cypher(q, {"name": safe_query})
        except Exception:
            return {"name": safe_query, "diseases": []}

        if not result:
            return {"name": safe_query, "diseases": []}

        # 查询不宜食用的疾病
        disease_q = """
        MATCH (f:Food {name: $name})<-[:no_eat]-(d:Disease)
        RETURN collect(d.name) AS diseases
        """
        try:
            dr = await run_cypher(disease_q, {"name": safe_query})
            diseases = dr[0]["diseases"] if dr else []
        except Exception:
            diseases = []

        return {"name": safe_query, "diseases": diseases}

    else:
        # 按疾病查询
        q = """
        MATCH (d:Disease {name: $name})
        RETURN d.name AS name
        """
        try:
            result = await run_cypher(q, {"name": safe_query})
        except Exception:
            return {"name": safe_query, "doEat": [], "noEat": [], "recommandEat": []}

        if not result:
            return {"name": safe_query, "doEat": [], "noEat": [], "recommandEat": []}

        info = {"name": safe_query}

        # 合并查询：宜吃、忌吃、推荐食物
        combined_q = """
        MATCH (d:Disease {name: $name})
        OPTIONAL MATCH (d)-[:do_eat]->(f1:Food)
        OPTIONAL MATCH (d)-[:no_eat]->(f2:Food)
        OPTIONAL MATCH (d)-[:recommand_eat]->(f3:Food)
        RETURN collect(DISTINCT f1.name) AS doEat,
          collect(DISTINCT f2.name) AS noEat,
          collect(DISTINCT f3.name) AS recommandEat
        """
        try:
            cr = await run_cypher(combined_q, {"name": safe_query})
            if cr:
                r = cr[0]
                info["doEat"] = (r["doEat"] or [])[:20]
                info["noEat"] = (r["noEat"] or [])[:20]
                info["recommandEat"] = (r["recommandEat"] or [])[:20]
            else:
                info["doEat"] = []
                info["noEat"] = []
                info["recommandEat"] = []
        except Exception:
            info["doEat"] = []
            info["noEat"] = []
            info["recommandEat"] = []

        return info


@app.post("/api/drug/interaction")
async def drug_interaction(req: DrugInteractionRequest):
    drugs = [sanitize_input(d) for d in req.drugs if d.strip()]
    if len(drugs) < 2:
        raise HTTPException(status_code=400, detail="请至少提供两种药品")

    # 查询药品之间通过疾病建立的间接关系
    interactions = []
    for i in range(len(drugs)):
        for j in range(i + 1, len(drugs)):
            query = """
            MATCH (d1:Drug {name: $drug1})<-[:common_drug]-(dis:Disease)-[:common_drug]->(d2:Drug {name: $drug2})
            RETURN dis.name AS disease
            LIMIT 5
            """
            try:
                results = await run_cypher(query, {"drug1": drugs[i], "drug2": drugs[j]})
                if results:
                    diseases = [r["disease"] for r in results]
                    interactions.append({
                        "drug1": drugs[i],
                        "drug2": drugs[j],
                        "risk": "中",
                        "description": f"两种药品均可用于治疗{'、'.join(diseases)}，同时使用前请咨询医生。",
                    })
                else:
                    interactions.append({
                        "drug1": drugs[i],
                        "drug2": drugs[j],
                        "risk": "低",
                        "description": "未发现已知的药物相互作用，但建议遵医嘱使用。",
                    })
            except Exception:
                interactions.append({
                    "drug1": drugs[i],
                    "drug2": drugs[j],
                    "risk": "未知",
                    "description": "暂无相关数据。",
                })

    return {"interactions": interactions}


# ========== 就医指南接口 ==========
@app.get("/api/guide/department")
async def guide_department(query: str):
    safe_query = sanitize_input(query)

    # 合并查询：科室及其关联的疾病和检查
    q = """
    MATCH (d:Disease)-[:belongs_to]->(dep:Department)
    WHERE d.name CONTAINS $query
    WITH DISTINCT dep
    OPTIONAL MATCH (d2:Disease)-[:belongs_to]->(dep)
    OPTIONAL MATCH (d2)-[:need_check]->(c:Check)
    RETURN dep.name AS name,
      collect(DISTINCT d2.name) AS diseases,
      collect(DISTINCT c.name) AS checks
    LIMIT 5
    """
    try:
        results = await run_cypher(q, {"query": safe_query})
    except Exception:
        results = []

    if not results or not results[0].get("name"):
        # 查症状关联的疾病对应的科室
        q2 = """
        MATCH (s:Symptom)<-[:has_symptom]-(d:Disease)-[:belongs_to]->(dep:Department)
        WHERE s.name CONTAINS $query
        WITH DISTINCT dep
        OPTIONAL MATCH (d2:Disease)-[:belongs_to]->(dep)
        OPTIONAL MATCH (d2)-[:need_check]->(c:Check)
        RETURN dep.name AS name,
          collect(DISTINCT d2.name) AS diseases,
          collect(DISTINCT c.name) AS checks
        LIMIT 5
        """
        try:
            results = await run_cypher(q2, {"query": safe_query})
        except Exception:
            results = []

    departments = []
    for r in results:
        if not r.get("name"):
            continue
        departments.append({
            "name": r["name"],
            "description": f"{r['name']}是医院的重要科室",
            "diseases": (r.get("diseases") or [])[:10],
            "checks": (r.get("checks") or [])[:10],
        })

    return {"departments": departments}


@app.get("/api/guide/check")
async def guide_check(query: str):
    safe_query = sanitize_input(query)

    q = """
    MATCH (c:Check {name: $name})
    RETURN c.name AS name, properties(c) AS props
    """
    try:
        results = await run_cypher(q, {"name": safe_query})
    except Exception:
        results = []

    if not results:
        # 模糊搜索
        fuzzy_q = """
        MATCH (c:Check)
        WHERE c.name CONTAINS $name
        RETURN c.name AS name, properties(c) AS props
        LIMIT 1
        """
        try:
            results = await run_cypher(fuzzy_q, {"name": safe_query})
        except Exception:
            pass

    if not results:
        raise HTTPException(status_code=404, detail="未找到该检查项目")

    check_name = results[0]["name"]
    props = results[0]["props"]

    # 获取关联疾病
    disease_q = """
    MATCH (d:Disease)-[:need_check]->(c:Check {name: $name})
    RETURN collect(d.name) AS diseases
    """
    try:
        dr = await run_cypher(disease_q, {"name": check_name})
        related_diseases = dr[0]["diseases"][:10] if dr else []
    except Exception:
        related_diseases = []

    return {
        "name": check_name,
        "purpose": props.get("desc", "暂无"),
        "process": "请咨询医院了解具体检查流程",
        "precautions": "请遵医嘱",
        "normalRange": "请参考医院报告单",
        "relatedDiseases": related_diseases,
    }


# ========== 健康管理接口 ==========
@app.post("/api/health/prevention")
async def health_prevention(request: Request, username: str = Depends(get_current_user)):
    profile = profiles_db.get(username, {})
    body = await request.json()
    profile = body.get("profile") or profile

    age = profile.get("age", "未知")
    gender = profile.get("gender", "未知")
    family_history = profile.get("family_history", "无")
    medical_history = profile.get("medical_history", "无")
    allergy_drug = profile.get("allergy_drug", "无")

    prompt = f"""你是一位专业的健康管理医生。请根据以下用户健康档案，生成个性化的疾病预防计划。

用户档案：
- 年龄：{age}岁
- 性别：{gender}
- 家族病史：{family_history}
- 既往病史：{medical_history}
- 药品过敏：{allergy_drug}

请以 JSON 格式返回，结构如下（不要包含 markdown 代码块标记）：
{{
  "items": [
    {{
      "disease": "疾病名称",
      "reason": "为什么该用户需要预防此疾病（结合档案说明）",
      "measures": ["预防措施1", "预防措施2", "预防措施3", "预防措施4"]
    }}
  ],
  "dailyTips": {{
    "diet": "针对该用户的饮食建议",
    "exercise": "针对该用户的运动建议",
    "rest": "针对该用户的作息建议"
  }}
}}

要求：
1. 根据用户年龄、性别、家族病史等个性化推荐 3-5 种需重点预防的疾病
2. 每个疾病的预防措施要具体、可操作
3. 日常建议要符合用户个人情况"""

    if DEEPSEEK_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(
                    DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": "你是专业的医疗健康助手，返回纯 JSON，不要 markdown 代码块。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 2000,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    # 清理可能的 markdown 代码块
                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                    if content.startswith("json"):
                        content = content[4:].strip()
                    result = json.loads(content)
                    return result
                else:
                    logger.warning(f"DeepSeek API 返回 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"AI 生成预防计划失败: {e}\n{traceback.format_exc()}")

    # 降级方案：基于档案硬编码
    items = []
    if "糖尿病" in family_history:
        items.append({"disease": "糖尿病", "reason": "家族中有糖尿病病史，患病风险较高", "measures": ["控制饮食，减少糖分摄入", "定期检测血糖", "保持适量运动", "控制体重"]})
    if "高血压" in family_history:
        items.append({"disease": "高血压", "reason": "家族中有高血压病史", "measures": ["减少盐分摄入", "保持规律作息", "适度运动", "定期测量血压"]})
    if age and str(age).isdigit() and int(age) > 40:
        items.append({"disease": "心脑血管疾病", "reason": f"{age}岁属于高发年龄段", "measures": ["定期体检", "控制三高", "戒烟限酒", "保持良好心态"]})
    items.append({"disease": "感冒", "reason": "最常见的呼吸道疾病", "measures": ["勤洗手", "避免接触患者", "增强免疫力", "注意保暖"]})
    return {"items": items, "dailyTips": {"diet": "均衡饮食，多吃蔬菜水果", "exercise": "每周至少150分钟中等强度运动", "rest": "保证7-8小时睡眠"}}


@app.post("/api/health/chronic")
async def health_chronic(request: Request, username: str = Depends(get_current_user)):
    body = await request.json()
    disease = body.get("disease", "")
    profile = body.get("profile") or profiles_db.get(username, {})

    age = profile.get("age", "未知")
    gender = profile.get("gender", "未知")
    medical_history = profile.get("medical_history", "无")
    allergy_drug = profile.get("allergy_drug", "无")

    prompt = f"""你是一位专业的慢性病管理医生。请为用户生成「{disease}」的个性化管理计划。

用户档案：
- 年龄：{age}岁
- 性别：{gender}
- 既往病史：{medical_history}
- 药品过敏：{allergy_drug}

请以 JSON 格式返回（不要包含 markdown 代码块标记）：
{{
  "name": "{disease}管理计划",
  "goal": "具体的管理目标",
  "diet": ["饮食建议1", "饮食建议2", "饮食建议3", "饮食建议4"],
  "exercise": ["运动建议1", "运动建议2", "运动建议3"],
  "checks": ["检查项目1", "检查项目2", "检查项目3"],
  "medicationReminder": "用药提醒（结合用户过敏史）"
}}

要求：
1. 饮食、运动、检查建议要具体、可执行
2. 用药提醒要考虑用户的药品过敏情况
3. 管理目标要量化"""

    if DEEPSEEK_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(
                    DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": "你是专业的医疗健康助手，返回纯 JSON，不要 markdown 代码块。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 2000,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                    if content.startswith("json"):
                        content = content[4:].strip()
                    result = json.loads(content)
                    return result
                else:
                    logger.warning(f"DeepSeek API 返回 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"AI 生成慢性病管理计划失败: {e}\n{traceback.format_exc()}")

    # 降级方案
    plans = {
        "高血压": {"name": "高血压管理计划", "goal": "将血压控制在140/90mmHg以下", "diet": ["低盐饮食，每日盐摄入<6g", "多吃富含钾的食物", "限制饮酒", "减少高脂肪食物"], "exercise": ["每天步行30分钟", "太极拳或瑜伽", "避免剧烈运动"], "checks": ["每周测量血压", "每月血脂检查", "每季度肾功能检查"], "medicationReminder": "请按时服用降压药，不要擅自停药"},
        "糖尿病": {"name": "糖尿病管理计划", "goal": "空腹血糖<7.0mmol/L，糖化血红蛋白<7%", "diet": ["控制总热量摄入", "少食多餐", "选择低GI食物", "增加膳食纤维"], "exercise": ["餐后30分钟开始运动", "每天步行或慢跑30分钟", "适当力量训练"], "checks": ["每日监测血糖", "每3个月检查糖化血红蛋白", "每年眼底检查"], "medicationReminder": "请按时服用降糖药，注意低血糖症状"},
    }
    if disease in plans:
        return plans[disease]
    return {"name": f"{disease}管理计划", "goal": "控制病情，提高生活质量", "diet": ["均衡饮食", "避免刺激性食物", "适量饮水"], "exercise": ["适度运动", "循序渐进", "避免过度劳累"], "checks": ["定期复查", "遵医嘱检查"], "medicationReminder": "请遵医嘱按时服药"}


@app.get("/api/health/records")
async def get_health_records(username: str = Depends(get_current_user)):
    records = health_records_db.get(username, [])
    # 为旧记录补上 _id
    changed = False
    for i, r in enumerate(records):
        if "_id" not in r:
            r["_id"] = f"{r.get('date', 'unknown')}_{i}_{int(time.time() * 1000)}"
            changed = True
    if changed:
        save_json(HEALTH_RECORDS_FILE, health_records_db)
    return {"records": records}


@app.post("/api/health/records")
async def save_health_record(record: HealthRecord, username: str = Depends(get_current_user)):
    if username not in health_records_db:
        health_records_db[username] = []
    rec = record.model_dump()
    rec["_id"] = f"{rec['date']}_{int(time.time() * 1000)}"
    health_records_db[username].append(rec)
    # 按日期排序
    health_records_db[username].sort(key=lambda x: x["date"], reverse=True)
    save_json(HEALTH_RECORDS_FILE, health_records_db)
    return {"message": "记录已保存"}


@app.delete("/api/health/records/{record_id}")
async def delete_health_record(record_id: str, username: str = Depends(get_current_user)):
    """删除指定健康记录"""
    if username not in health_records_db:
        raise HTTPException(status_code=404, detail="无记录")
    records = health_records_db[username]
    new_records = [r for r in records if r.get("_id") != record_id and r.get("date") != record_id]
    if len(new_records) == len(records):
        raise HTTPException(status_code=404, detail="未找到该记录")
    health_records_db[username] = new_records
    save_json(HEALTH_RECORDS_FILE, health_records_db)
    return {"message": "记录已删除"}


# ========== 健康计划存储接口 ==========
@app.get("/api/health/plans")
async def get_health_plans(username: str = Depends(get_current_user)):
    plans = health_plans_db.get(username, [])
    return {"plans": plans}


@app.post("/api/health/plans")
async def save_health_plan(request: Request, username: str = Depends(get_current_user)):
    body = await request.json()
    plan_type = body.get("type", "prevention")
    data = body.get("data", {})
    disease = body.get("disease", "")

    plan = {
        "_id": f"{plan_type}_{int(time.time() * 1000)}",
        "type": plan_type,
        "disease": disease,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    if username not in health_plans_db:
        health_plans_db[username] = []
    health_plans_db[username].insert(0, plan)
    save_json(HEALTH_PLANS_FILE, health_plans_db)
    return {"message": "计划已保存", "plan": plan}


@app.delete("/api/health/plans")
async def clear_health_plans(username: str = Depends(get_current_user)):
    health_plans_db[username] = []
    save_json(HEALTH_PLANS_FILE, health_plans_db)
    return {"message": "所有计划已清空"}


# ========== 知识百科接口 ==========
@app.get("/api/wiki/daily-tip")
async def daily_tip():
    # 每日随机推荐
    query = """
    MATCH (d:Disease)
    WITH d, rand() AS r
    ORDER BY r
    LIMIT 1
    RETURN d.name AS name, d.desc AS desc, d.prevent AS prevent
    """
    try:
        results = await run_cypher(query)
        if results:
            r = results[0]
            return {
                "title": r["name"],
                "content": f"{r['desc'] or '暂无简介'}。预防措施：{r['prevent'] or '暂无'}",
                "category": "Disease",
            }
    except Exception:
        pass

    return {
        "title": "感冒的预防与治疗",
        "content": "感冒是最常见的呼吸道疾病，由病毒引起。预防措施包括：勤洗手、避免接触患者、增强免疫力。治疗以对症治疗为主，注意休息和多饮水。",
        "category": "Disease",
    }


# ========== DeepSeek AI 问答接口 ==========
SYSTEM_PROMPT = """你是一个专业的医疗健康助手，必须优先使用提供的医疗知识图谱数据回答用户问题。
回答要求：
1. 所有涉及疾病、药品、症状的信息必须来自知识图谱，不确定的内容明确说明
2. 语言通俗易懂，避免专业术语堆砌
3. 禁止给出具体用药剂量和手术方案，只提供一般性建议
4. 所有回答末尾必须添加："以上内容仅供参考，如有不适请及时就医"
5. 结合用户提供的健康档案信息给出个性化建议
"""


@app.post("/api/chat")
async def chat(req: ChatRequest, username: str = Depends(optional_user)):
    # 构建消息
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 添加健康档案上下文
    if username and username in profiles_db:
        profile = profiles_db[username]
        parts = []
        if profile.get("age"):
            parts.append(f"{profile['age']}岁")
        if profile.get("gender"):
            parts.append(profile["gender"])
        if profile.get("allergy_drug"):
            parts.append(f"药品过敏：{profile['allergy_drug']}")
        if profile.get("medical_history"):
            parts.append(f"病史：{profile['medical_history']}")
        if profile.get("family_history"):
            parts.append(f"家族史：{profile['family_history']}")
        if parts:
            messages.append({
                "role": "system",
                "content": f"用户健康档案：{'，'.join(parts)}",
            })

    # 添加用户上下文
    if req.context:
        messages.append({"role": "system", "content": req.context})

    # 添加对话历史
    for msg in req.messages[-10:]:
        messages.append({"role": msg.role, "content": msg.content})

    # 如果没有配置API密钥，返回模拟响应
    if not DEEPSEEK_API_KEY:
        return await simulate_chat_response(req.messages[-1].content if req.messages else "")

    # 调用DeepSeek API
    async def generate():
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            try:
                async with client.stream(
                    "POST",
                    DEEPSEEK_API_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": messages,
                        "stream": True,
                        "temperature": 0.7,
                        "max_tokens": 2000,
                    },
                ) as response:
                    if response.status_code != 200:
                        yield f"data: {json.dumps({'content': 'AI服务暂时不可用，请稍后再试。'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                yield "data: [DONE]\n\n"
                                break
                            try:
                                chunk = json.loads(data)
                                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    yield f"data: {json.dumps({'content': content})}\n\n"
                            except Exception:
                                pass
            except Exception as e:
                yield f"data: {json.dumps({'content': f'AI服务请求失败：{str(e)}'})}\n\n"
                yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


async def simulate_chat_response(question: str):
    """模拟AI响应（当未配置DeepSeek API密钥时使用）"""

    # 尝试从知识图谱获取相关信息
    keywords = ["感冒", "发烧", "咳嗽", "头痛", "高血压", "糖尿病", "胃炎", "失眠"]
    matched_keyword = None
    for kw in keywords:
        if kw in question:
            matched_keyword = kw
            break

    if matched_keyword:
        # 从知识图谱查询
        query = """
        MATCH (d:Disease {name: $name})
        OPTIONAL MATCH (d)-[:has_symptom]->(s:Symptom)
        OPTIONAL MATCH (d)-[:common_drug]->(dr:Drug)
        OPTIONAL MATCH (d)-[:do_eat]->(f:Food)
        OPTIONAL MATCH (d)-[:belongs_to]->(dep:Department)
        RETURN d.name AS name, d.desc AS desc, d.cause AS cause, d.prevent AS prevent,
               collect(DISTINCT s.name) AS symptoms,
               collect(DISTINCT dr.name) AS drugs,
               collect(DISTINCT f.name) AS foods,
               collect(DISTINCT dep.name) AS departments
        """
        try:
            results = await run_cypher(query, {"name": matched_keyword})
            if results:
                r = results[0]
                response = f"## {r['name']}\n\n"
                if r["desc"]:
                    response += f"**简介：**{r['desc']}\n\n"
                if r["cause"]:
                    response += f"**病因：**{r['cause']}\n\n"
                if r["symptoms"]:
                    response += f"**常见症状：**{'、'.join(r['symptoms'][:10])}\n\n"
                if r["drugs"]:
                    response += f"**常用药品：**{'、'.join(r['drugs'][:10])}\n\n"
                if r["foods"]:
                    response += f"**宜吃食物：**{'、'.join(r['foods'][:10])}\n\n"
                if r["prevent"]:
                    response += f"**预防措施：**{r['prevent']}\n\n"
                if r["departments"]:
                    response += f"**就诊科室：**{'、'.join(r['departments'])}\n\n"
                response += "\n以上内容仅供参考，如有不适请及时就医"

                async def generate():
                    # 按每5个字符分块发送，减少HTTP请求数
                    chunk_size = 5
                    for i in range(0, len(response), chunk_size):
                        yield f"data: {json.dumps({'content': response[i:i+chunk_size]})}\n\n"
                        await asyncio.sleep(0.05)
                    yield "data: [DONE]\n\n"

                return StreamingResponse(generate(), media_type="text/event-stream")
        except Exception:
            pass

    # 默认响应
    default_response = f"您好！您询问的是关于「{question}」的问题。\n\n"
    default_response += "由于AI服务暂未配置API密钥，我基于知识图谱为您提供以下信息：\n\n"
    default_response += "建议您：\n"
    default_response += "1. 使用疾病自查功能，选择相关症状进行初步筛查\n"
    default_response += "2. 使用知识图谱功能，搜索相关疾病和药品信息\n"
    default_response += "3. 如有不适，请及时就医\n\n"
    default_response += "以上内容仅供参考，如有不适请及时就医"

    async def generate():
        chunk_size = 5
        for i in range(0, len(default_response), chunk_size):
            yield f"data: {json.dumps({'content': default_response[i:i+chunk_size]})}\n\n"
            await asyncio.sleep(0.05)
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ========== 聊天记录存储 ==========

class ChatHistoryMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None


class SaveChatRequest(BaseModel):
    session_id: str
    session_name: Optional[str] = "新对话"
    messages: List[ChatHistoryMessage]


@app.get("/api/chat/history")
async def get_chat_history(username: str = Depends(get_current_user)):
    """获取当前用户的所有聊天记录"""
    user_history = chat_history_db.get(username, {"sessions": []})
    return user_history


@app.post("/api/chat/history/save")
async def save_chat_history(req: SaveChatRequest, username: str = Depends(get_current_user)):
    """保存/更新一个对话会话"""
    if username not in chat_history_db:
        chat_history_db[username] = {"sessions": []}

    sessions = chat_history_db[username]["sessions"]
    # 查找是否已有该 session
    for i, s in enumerate(sessions):
        if s["id"] == req.session_id:
            sessions[i] = {
                "id": req.session_id,
                "name": req.session_name,
                "messages": [m.model_dump() for m in req.messages],
            }
            save_json(CHAT_HISTORY_FILE, chat_history_db)
            return {"ok": True}

    # 新会话，追加到头部
    sessions.insert(0, {
        "id": req.session_id,
        "name": req.session_name,
        "messages": [m.model_dump() for m in req.messages],
    })
    save_json(CHAT_HISTORY_FILE, chat_history_db)
    return {"ok": True}


@app.delete("/api/chat/history")
async def clear_chat_history(username: str = Depends(get_current_user)):
    """一键清除当前用户所有聊天记录"""
    chat_history_db[username] = {"sessions": []}
    save_json(CHAT_HISTORY_FILE, chat_history_db)
    return {"ok": True}


# ========== 启动 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
