# coding: utf-8
"""知识图谱路由（阶段五物理拆分）：实体检索/详情/路径/关联/邻居。含游标翻页与详情缓存。"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.config import ENTITY_CACHE_TTL
from core.cache import get_cache as _get_cache, entity_cache_key
from services.graph_db import run_cypher
from deps import (
    _validate_entity_input, _alias_candidates, _name_match_union,
    ALLOWED_LABELS, _cache_get, _cache_set,
)

router = APIRouter()
logger = logging.getLogger("app.kg")


# ========== 知识图谱接口 ==========
# （实体输入校验 / 标签白名单 / 别名候选 / 跨标签定位 Cypher 生成 已抽至 deps.py）


@router.get("/api/kg/entities")
async def search_entities(
    search: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    page: int = Query(default=1, ge=1),
    cursor: Optional[str] = Query(default=None),
):
    # 输入校验：超过200字符返回400；空搜索词视为不带关键词过滤（保持原有行为）
    safe_search = _validate_entity_input(search, "搜索词")
    safe_type = _validate_entity_input(type, "实体类型")

    # 翻页（阶段四）：保留既有 page/limit 契约；新增可选 cursor 游标参数做高效深翻页。
    # 游标依赖 name 的稳定排序（下方所有查询统一 ORDER BY n.name，走阶段二建立的有序
    # 索引；Disease 为普通索引亦支持 ORDER BY）。传 cursor 时忽略 page，用
    # name > $cursor 定位代替大 OFFSET，深翻页耗时平稳；不传时与原页码翻页等价
    # （仅额外补上 ORDER BY，使原先无序 SKIP 分页结果稳定，不改变字段契约）。
    skip = (page - 1) * limit

    async def _do_search(search_term, cursor_val):
        """按单个搜索词执行实体检索，返回 (nodes, links, total)

        cursor_val 非空 → 游标模式（WHERE n.name > $cursor 定位、不带 SKIP）；
        为空 → 页码模式（SKIP $skip LIMIT $limit）。两种模式统一 ORDER BY n.name，
        保证翻页稳定（游标定位本身依赖该全序）。
        """
        use_cursor = bool(cursor_val)
        # 分页子句：游标模式省 SKIP，页码模式保留 SKIP
        order_paging = "ORDER BY n.name " + ("" if use_cursor else "SKIP $skip ") + "LIMIT $limit"
        params = {"limit": limit}
        if use_cursor:
            params["cursor"] = cursor_val
        else:
            params["skip"] = skip

        if search_term and safe_type:
            query = (
                "MATCH (n) WHERE n.name CONTAINS $search AND $type IN labels(n) "
                + ("AND n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )
            params.update({"search": search_term, "type": safe_type})
        elif search_term:
            query = (
                "MATCH (n) WHERE n.name CONTAINS $search "
                + ("AND n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )
            params.update({"search": search_term})
        elif safe_type:
            # Neo4j 标签不可参数化，仅允许白名单内的标签拼接进 Cypher；
            # 非白名单标签返回空结果（保持既有行为兼容，不改为 400）
            if safe_type not in ALLOWED_LABELS:
                return [], [], 0
            query = (
                f"MATCH (n:{safe_type}) "
                + ("WHERE n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )
        else:
            query = (
                "MATCH (n) "
                + ("WHERE n.name > $cursor " if use_cursor else "")
                + "RETURN n.name AS name, labels(n)[0] AS label, properties(n) AS props "
                + order_paging
            )

        try:
            results = await run_cypher(query, params)
        except Exception as e:
            logger.error(f"实体搜索失败: {e}")
            return [], [], 0

        inner_nodes = []
        for r in results:
            inner_nodes.append({
                "name": r["name"],
                "label": r["label"],
                "desc": r["props"].get("desc", ""),
            })

        # 获取关联边
        if len(inner_nodes) > 0:
            names = [n["name"] for n in inner_nodes[:50]]  # 限制边查询数量
            link_query = """
            MATCH (a)-[r]->(b)
            WHERE a.name IN $names AND b.name IN $names
            RETURN a.name AS source, b.name AS target, type(r) AS relType
            LIMIT 200
            """
            try:
                link_results = await run_cypher(link_query, {"names": names})
                inner_links = [{"source": l["source"], "target": l["target"], "relType": l["relType"]} for l in link_results]
            except Exception as e:
                logger.error(f"关联边查询失败: {e}")
                inner_links = []
        else:
            inner_links = []

        # 获取总数（缓存5分钟）
        cached_total = _cache_get("kg_total_count", ttl=300)
        if cached_total is not None:
            inner_total = cached_total
        else:
            count_query = "MATCH (n) RETURN count(n) AS total"
            try:
                count_result = await run_cypher(count_query)
                inner_total = count_result[0]["total"] if count_result else 0
                _cache_set("kg_total_count", inner_total)
            except Exception as e:
                logger.error(f"总数查询失败: {e}")
                inner_total = len(inner_nodes)

        return inner_nodes, inner_links, inner_total

    # 别名归一化（阶段三修正——模糊搜索两路合并）：
    # CONTAINS 模糊搜索场景下，归一化名命中的结果与原词命中的结果「合并」而非二选一：
    # 分别用规范名与原词各查一路，按 name 去重后合并返回（避免搜「感冒」只返回
    # 「上呼吸道感染」相关实体而丢失所有名称含「感冒」的实体）；
    # 合并后按原 limit 截断，保持分页语义；total 为全库实体总数，两路相同；
    # 无别名映射（候选只有原词）或无搜索词时走单路原逻辑，行为不变。
    # 精确匹配类接口不受影响，仍保持「规范名优先、未命中原词兜底」语义。
    nodes, links, total = [], [], 0
    search_terms = _alias_candidates(safe_search)
    single_path = len(search_terms) <= 1
    if single_path:
        nodes, links, total = await _do_search(search_terms[0], cursor)
    else:
        merged_nodes, merged_links = [], []
        seen_names = set()
        seen_links = set()
        for term in search_terms:
            # 别名两路合并模式下禁用游标（各路按页码取回后合并去重），故传 cursor_val=None
            t_nodes, t_links, t_total = await _do_search(term, None)
            total = t_total or total
            for n in t_nodes:
                # 按实体名去重（同名实体以先返回的一路为准，保留其标签与简介）
                if n["name"] not in seen_names:
                    seen_names.add(n["name"])
                    merged_nodes.append(n)
            for l in t_links:
                link_key = (l["source"], l["target"], l["relType"])
                if link_key not in seen_links:
                    seen_links.add(link_key)
                    merged_links.append(l)
        # 合并后按原 limit 截断（两路各自已带分页参数，合并去重后再收口到单页容量）
        nodes = merged_nodes[:limit]
        links = merged_links

    # next_cursor：仅单路、且本页取满 limit 时给出本页末实体名作为续翻游标；
    # 别名两路合并模式不做游标深翻（返回 None，页码翻页仍可用）。无更多数据时为 None。
    next_cursor = nodes[-1]["name"] if (single_path and nodes and len(nodes) >= limit) else None
    return {"nodes": nodes, "links": links, "total": total, "next_cursor": next_cursor}


@router.get("/api/kg/entity/{name}")
async def get_entity_detail(name: str):
    # 路径参数为空时按未命中处理返回 404（保持既有行为）；超长返回 400；
    # 查询本身已参数化（$name），原始值直接传入即可，无需黑名单过滤
    entity_name = _validate_entity_input(name, "实体名称")
    if not entity_name:
        raise HTTPException(status_code=404, detail="实体不存在")
    # 实体详情缓存（阶段四）：图谱为只读数据，按请求实体名缓存已构造的响应对象，
    # 命中直接返回，跳过 5 路 OPTIONAL MATCH 聚合查询；未命中（404）不缓存。
    _cache = _get_cache()
    cached_entity = _cache.get(entity_cache_key(entity_name))
    if cached_entity is not None:
        return cached_entity
    # 实体定位改为逐标签分支（_name_match_union）：原无标签 MATCH (n {name:$name})
    # 为全表扫描，逐标签后各分支走标签约束索引；后续 OPTIONAL MATCH 子句（含 s2 子句）语义不变
    query = (
        _name_match_union("n", "name") + "\n"
        "OPTIONAL MATCH (n)-[:has_symptom]->(s:Symptom)\n"
        "OPTIONAL MATCH (n)-[:common_drug]->(dr:Drug)\n"
        "OPTIONAL MATCH (n)-[:do_eat]->(f:Food)\n"
        "OPTIONAL MATCH (n)-[:need_check]->(c:Check)\n"
        "OPTIONAL MATCH (s2:Symptom)<-[:has_symptom]-(d:Disease)\n"
        "WHERE s2.name = n.name\n"
        "RETURN n, labels(n)[0] AS label,\n"
        "  collect(DISTINCT s.name) AS symptoms,\n"
        "  collect(DISTINCT dr.name) AS drugs,\n"
        "  collect(DISTINCT f.name) AS foods,\n"
        "  collect(DISTINCT c.name) AS checks,\n"
        "  collect(DISTINCT d.name) AS diseases\n"
    )
    # 别名归一化：按候选序列（规范名优先、原词兜底）逐个精确查询，命中即返回；
    # 全部未命中时保持既有 404 契约；查询异常保持既有 500 契约（仅对最后一次尝试抛出）
    results = []
    matched_name = entity_name
    for idx, candidate in enumerate(_alias_candidates(entity_name)):
        try:
            results = await run_cypher(query, {"name": candidate})
        except Exception as e:
            logger.error(f"实体详情查询失败: {e}")
            if idx == len(_alias_candidates(entity_name)) - 1:
                raise HTTPException(status_code=500, detail="查询失败")
            results = []
        if results:
            matched_name = candidate
            break

    if not results:
        raise HTTPException(status_code=404, detail="实体不存在")

    r = results[0]
    node = r["n"]
    label = r["label"]
    props = dict(node) if hasattr(node, 'items') else {}
    # 剔除向量属性 embedding（阶段三为向量检索新增），避免其流入实体详情响应与
    # 前端「全属性」通用渲染（Drug/Food 等走 properties 列表展示，向量数组会刷屏）
    props.pop("embedding", None)

    entity = {
        "name": matched_name,
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

    _cache.set(entity_cache_key(entity_name), entity, ttl=ENTITY_CACHE_TTL)
    return entity


@router.get("/api/kg/path")
async def find_path(source: str, target: str, max_depth: int = Query(default=5, ge=1, le=10)):
    # 空输入直接返回空路径结果，保持“未命中返回空结构”行为；超长返回 400；
    # source/target 通过参数化（$source/$target）传入，无需黑名单过滤
    safe_source = _validate_entity_input(source, "起始实体")
    safe_target = _validate_entity_input(target, "目标实体")
    if not safe_source or not safe_target:
        return {"paths": []}

    # Neo4j 变长关系上限不可参数化，depth 已由 FastAPI Query(ge/le) 约束为 int，
    # 此处显式强转并做范围校验，双保险防止拼接注入
    max_depth = int(max_depth)
    if not 1 <= max_depth <= 10:
        raise HTTPException(status_code=400, detail="max_depth 必须在 1 到 10 之间")

    # 起终点实体定位改为逐标签分支（_name_match_union），各分支命中标签约束索引；
    # shortestPath 在已锚定的 a、b 节点集合间展开，语义与原无标签写法一致（变长上限仍由 max_depth 控制）
    query = (
        _name_match_union("a", "source") + "\n"
        + _name_match_union("b", "target") + "\n"
        + "MATCH path = shortestPath((a)-[*.." + str(max_depth) + "]->(b))\n"
        + "RETURN [x IN nodes(path) | x.name] AS nodeNames,\n"
        + "       [r IN relationships(path) | type(r)] AS relTypes\n"
        + "LIMIT 5"
    )
    try:
        # 别名归一化：起点/终点各自按（规范名优先、原词兜底）候选展开，
        # 组合按序尝试，命中路径即返回；全部未命中保持空路径契约（最多4次查询）
        results = []
        for source_candidate in _alias_candidates(safe_source):
            for target_candidate in _alias_candidates(safe_target):
                results = await run_cypher(
                    query, {"source": source_candidate, "target": target_candidate}
                )
                if results:
                    break
            if results:
                break
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


@router.get("/api/kg/related")
async def get_related_entities(entity: str, depth: int = Query(default=1, ge=1, le=3)):
    # 空输入直接返回空结构，保持既有行为；超长返回 400；实体名经参数化（$entity）传入
    safe_entity = _validate_entity_input(entity, "实体名称")
    if not safe_entity:
        return {"nodes": [], "links": []}

    # Neo4j 变长关系上限不可参数化，depth 已由 FastAPI Query(ge/le) 约束为 int，
    # 此处显式强转并做范围校验，双保险防止拼接注入
    depth = int(depth)
    if not 1 <= depth <= 3:
        raise HTTPException(status_code=400, detail="depth 必须在 1 到 3 之间")

    # 实体定位改为逐标签分支（_name_match_union），命中标签约束索引；后续展开逻辑不变
    if depth == 1:
        query = (
            _name_match_union("n", "entity") + "\n"
            "MATCH (n)-[r]-(m)\n"
            "RETURN DISTINCT m.name AS name, labels(m)[0] AS label\n"
            "LIMIT 80"
        )
    else:
        query = (
            _name_match_union("n", "entity") + "\n"
            "MATCH (n)-[*1.." + str(depth) + "]-(m)\n"
            "RETURN DISTINCT m.name AS name, labels(m)[0] AS label\n"
            "LIMIT 200"
        )

    try:
        # 别名归一化：规范名优先，原词兜底（未命中再用原词重查）
        results = []
        effective_entity = safe_entity
        for candidate in _alias_candidates(safe_entity):
            try:
                results = await run_cypher(query, {"entity": candidate})
            except Exception as e:
                logger.error(f"关联实体查询失败: {e}")
                results = []
            if results:
                effective_entity = candidate
                break
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

    # 获取根节点的实际标签（基于实际命中的实体名；同样走逐标签索引定位）
    root_query = _name_match_union("n", "entity") + "\nRETURN labels(n)[0] AS label LIMIT 1"
    try:
        root_result = await run_cypher(root_query, {"entity": effective_entity})
        root_label = root_result[0]["label"] if root_result else "Disease"
    except Exception:
        root_label = "Disease"
    nodes.insert(0, {"name": effective_entity, "label": root_label})

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


@router.get("/api/kg/neighbors")
async def get_neighbors(
    name: str,
    depth: int = Query(default=1, ge=1, le=2),
    limit: int = Query(default=50, le=200),
):
    """增量拉取某实体的邻居子图（阶段四，供图谱页「展开下一跳」增量合并渲染）。

    与 /api/kg/related 并存（不改动后者契约）：本接口逐跳独立 LIMIT 逐层扩展，
    返回 {nodes, links}（含根节点自身），前端可合并进已有图而非整体重绘。
    - name 为空→空结构；超过200字符→400（复用 _validate_entity_input）；
    - depth 限 1|2，每跳各带一次 LIMIT（避免变长路径一次性展开爆炸）；
    - 别名归一化：规范名优先、原词兜底定位根节点；标签交替锚定 a 走标签索引。
    """
    safe_name = _validate_entity_input(name, "实体名称")
    if not safe_name:
        return {"nodes": [], "links": []}
    depth = int(depth)
    limit = int(limit)

    # 定位根实体（别名候选，规范名优先），确定实际命中名与标签；未命中→空结构
    root_label = None
    effective = safe_name
    root_query = _name_match_union("n", "entity") + "\nRETURN labels(n)[0] AS label LIMIT 1"
    for candidate in _alias_candidates(safe_name):
        try:
            rr = await run_cypher(root_query, {"entity": candidate})
        except Exception as e:
            logger.error(f"邻居根节点定位失败: {e}")
            rr = []
        if rr:
            effective = candidate
            root_label = rr[0]["label"]
            break
    if root_label is None:
        return {"nodes": [], "links": []}

    # 七类标签交替：让展开起点 a 命中阶段二建立的标签索引，避免无标签全表匹配
    label_alternation = ":" + "|".join(sorted(ALLOWED_LABELS))
    nodes = [{"name": effective, "label": root_label}]
    links = []
    seen = {effective}
    frontier = [effective]
    for _ in range(depth):
        if not frontier:
            break
        # 从当前层沿无向关系扩展一跳，排除已收集节点，本跳独立 LIMIT
        hop_query = (
            f"MATCH (a{label_alternation})-[r]-(b)\n"
            "WHERE a.name IN $names AND NOT b.name IN $visited\n"
            "RETURN b.name AS name, labels(b)[0] AS label, a.name AS from_name,\n"
            "       type(r) AS relType\n"
            "LIMIT $limit"
        )
        try:
            rows = await run_cypher(hop_query, {"names": frontier, "visited": list(seen), "limit": limit})
        except Exception as e:
            logger.error(f"邻居扩展失败: {e}")
            break
        new_frontier = []
        for r in rows:
            nm = r.get("name")
            if not nm or nm in seen:
                continue
            seen.add(nm)
            nodes.append({"name": nm, "label": r.get("label")})
            links.append({"source": r["from_name"], "target": nm, "relType": r.get("relType")})
            new_frontier.append(nm)
        frontier = new_frontier

    return {"nodes": nodes, "links": links}
