# coding: utf-8
"""
阶段二图谱数据迁移脚本（幂等、只增不删、可重复执行）。

迁移内容：
1. Disease 节点补 icd10 占位（空字符串）：待权威数据源（国家卫健委/WHO ICD-10
   官方对照表）对齐后回填，当前不编造编码值；
2. Drug 节点补 atc 占位（空字符串）：待 ATC 官方索引对齐后回填，当前不编造；
3. 六类核心关系（以库中实际存在者为准）补缺失属性：
   weight=1.0（默认等权，无可靠来源，仅供后续加权检索使用）、
   source="medical.json"（数据来源标注）、
   evidence_level="unverified"（未经临床证据核验）；
4. 从 data/medical.json 读取每疾病的 get_prob（形如 "0.00002%"，解析为
   浮点百分比数值，单位仍为 %），幂等写入 Disease 节点；
5. 症状 IDF：idf = log(1 + 总疾病数 / 关联该症状的疾病数)，
   语义：越常见的症状区分度越低、权重越低；无任何疾病关联的症状
   按"最罕见"处理给默认值 log(1 + 总疾病数)。

约束遵守：
- 全部为「仅补缺失属性」或「同源幂等覆写」，不删除任何节点/关系/属性；
- 分批提交（每批 10000 条），单批失败即中止并打印已完成进度，可重跑续做；
- 连接信息复用 backend/core/config.py（读取 backend/.env），不硬编码密钥。

用法：
    cd backend
    python scripts/migrate_graph_phase2.py
"""

import logging
import math
import os
import re
import sys

# 使本脚本可从项目任意位置运行：把 backend 目录加入模块搜索路径，
# 复用 core.config 的 .env 加载与安全校验（含 NEO4J_PASSWORD 必填校验）
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from neo4j import GraphDatabase  # noqa: E402

from core.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate_graph_phase2")

# 单批提交条数（分批事务，避免单个超大事务）
BATCH_SIZE = 10000

# 需要补属性的六类核心关系（执行前会先查 db.relationshipTypes()，只处理库中实际存在的类型）
TARGET_REL_TYPES = [
    "has_symptom",     # 疾病-症状
    "common_drug",     # 疾病-常用药品
    "do_eat",          # 疾病-宜吃食物
    "no_eat",          # 疾病-忌吃食物
    "need_check",      # 疾病-诊断检查
    "acompany_with",   # 疾病-并发症
]

# get_prob 解析正则：取字符串中第一个「数字%」片段；
# 语料中存在 "0.00001%(男同性恋尤为多见)"、"1%--3%"、"约为10%" 等自由文本，
# 统一取首个数值作为发病率近似（百分比数值，单位 %），不臆造其余含义
_PROB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def parse_get_prob(raw):
    """把形如 "0.00002%" 的字符串解析为浮点百分比数值（如 2e-05）；
    无法解析（空串/纯文字描述）返回 None，由调用方跳过计数"""
    if not isinstance(raw, str):
        return None
    m = _PROB_RE.search(raw)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def run_batch(session, query, params=None):
    """执行单批更新并返回首行首列的计数值"""
    record = session.run(query, params or {}).single()
    return record[0] if record else 0


def step_fill_node_placeholder(driver, label, prop, summary):
    """为指定标签节点补缺失占位属性（空字符串），分批幂等执行"""
    query = (
        f"MATCH (n:{label}) WHERE n.{prop} IS NULL "
        f"WITH n LIMIT $batch SET n.{prop} = '' "
        f"RETURN count(n)"
    )
    total = 0
    with driver.session() as session:
        while True:
            updated = run_batch(session, query, {"batch": BATCH_SIZE})
            if updated == 0:
                break
            total += updated
            logger.info("[%s.%s 占位] 本批 %d 条，累计 %d 条", label, prop, updated, total)
    summary.append((f"{label}.{prop} 占位", total))
    logger.info("[%s.%s 占位] 完成，共写入 %d 条（空串占位，待权威数据源回填）", label, prop, total)


def step_fill_rel_props(driver, rel_type, summary):
    """为指定关系类型补缺失的 weight / source / evidence_level（coalesce 只补缺失值）"""
    # 关系类型来自脚本内固定白名单（非外部输入），以模板拼接；无参数可注入点
    query = (
        f"MATCH ()-[r:{rel_type}]->() "
        f"WHERE r.weight IS NULL OR r.source IS NULL OR r.evidence_level IS NULL "
        f"WITH r LIMIT $batch "
        f"SET r.weight = coalesce(r.weight, 1.0), "
        f"    r.source = coalesce(r.source, 'medical.json'), "
        f"    r.evidence_level = coalesce(r.evidence_level, 'unverified') "
        f"RETURN count(r)"
    )
    total = 0
    with driver.session() as session:
        while True:
            updated = run_batch(session, query, {"batch": BATCH_SIZE})
            if updated == 0:
                break
            total += updated
            logger.info("[%s 关系属性] 本批 %d 条，累计 %d 条", rel_type, updated, total)
    summary.append((f"{rel_type} 关系属性", total))
    logger.info("[%s 关系属性] 完成，共补齐 %d 条", rel_type, total)


def step_write_get_prob(driver, summary):
    """从 data/medical.json 读取疾病患病率并幂等写入 Disease.get_prob（百分比数值）"""
    data_path = os.path.join(os.path.dirname(_BACKEND_DIR), "data", "medical.json")
    import json

    items = []          # (疾病名, 百分比数值)
    unparseable = 0     # 语料中 get_prob 缺失或无法解析为数值
    with open(data_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            name = record.get("name")
            value = parse_get_prob(record.get("get_prob"))
            if not name:
                continue
            if value is None:
                unparseable += 1
                continue
            items.append((name, value))
    logger.info("[get_prob] 语料共解析出 %d 条可用患病率，%d 条缺失/无法解析（跳过，不编造）",
                len(items), unparseable)

    # 同源幂等覆写：值恒来自 medical.json，重跑结果不变
    write_query = (
        "UNWIND $items AS it "
        "MATCH (d:Disease {name: it.name}) "
        "SET d.get_prob = it.value "
        "RETURN count(d)"
    )
    written = 0
    with driver.session() as session:
        for start in range(0, len(items), BATCH_SIZE):
            batch = [
                {"name": name, "value": value}
                for name, value in items[start:start + BATCH_SIZE]
            ]
            written += run_batch(session, write_query, {"items": batch})
            logger.info("[get_prob] 已处理 %d/%d", min(start + BATCH_SIZE, len(items)), len(items))

    unmatched = len(items) - written
    summary.append(("Disease.get_prob 写入", written))
    # 写入节点数可能大于语料条数：库中存在同名疾病节点（如重名未清洗），
    # MATCH 按名匹配会同时命中多个节点，属预期行为，不是数据错误
    logger.info("[get_prob] 完成：写入 %d 条节点，语料中未对上疾病名 %d 条，无法解析跳过 %d 条",
                written, max(unmatched, 0), unparseable)


def step_write_symptom_idf(driver, summary):
    """症状 IDF：idf = log(1 + 总疾病数/关联疾病数)；越常见的症状权重越低。
    无疾病关联的症状按最罕见处理，取最大 IDF（log(1 + 总疾病数)）"""
    with driver.session() as session:
        total_diseases = run_batch(
            session, "MATCH (d:Disease) RETURN count(DISTINCT d)"
        )
        if total_diseases <= 0:
            logger.warning("[idf] 图谱中无疾病节点，跳过症状 IDF 计算")
            summary.append(("Symptom.idf 写入", 0))
            return
        # 有疾病关联的症状：按关联疾病数计算（分批更新，聚合后再按名字分批写入）
        calc_query = (
            "MATCH (s:Symptom)<-[:has_symptom]-(d:Disease) "
            "WITH s.name AS name, count(DISTINCT d) AS dc "
            "RETURN name, dc"
        )
        pairs = [dict(r) for r in session.run(calc_query)]
        logger.info("[idf] 总疾病数 %d，参与计算的症状 %d 个", total_diseases, len(pairs))

        write_query = (
            "UNWIND $items AS it "
            "MATCH (s:Symptom {name: it.name}) "
            "SET s.idf = it.idf "
            "RETURN count(s)"
        )
        written = 0
        for start in range(0, len(pairs), BATCH_SIZE):
            batch = [
                {"name": p["name"], "idf": math.log(1.0 + total_diseases / p["dc"])}
                for p in pairs[start:start + BATCH_SIZE]
            ]
            written += run_batch(session, write_query, {"items": batch})
        summary.append(("Symptom.idf 写入（有疾病关联）", written))
        logger.info("[idf] 有疾病关联的症状写入 %d 个", written)

        # 无任何疾病关联的症状：默认最高 IDF（视为最罕见），保证诊断加权可用
        default_idf = math.log(1.0 + total_diseases)
        orphan_updated = run_batch(
            session,
            "MATCH (s:Symptom) WHERE NOT (s)<-[:has_symptom]-(:Disease) "
            "SET s.idf = $idf RETURN count(s)",
            {"idf": default_idf},
        )
        summary.append(("Symptom.idf 默认值（无疾病关联）", orphan_updated))
        logger.info("[idf] 无疾病关联的症状写入默认值 %d 个（默认 idf=%.4f）",
                    orphan_updated, default_idf)


def main():
    logger.info("开始阶段二图谱迁移（幂等、只增不删），目标库: %s", NEO4J_URI)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    summary = []
    try:
        # 预检：仅处理库中实际存在的关系类型，避免对不存在的类型执行无效更新
        with driver.session() as session:
            existing_types = {
                r["relationshipType"]
                for r in session.run(
                    "CALL db.relationshipTypes() YIELD relationshipType "
                    "RETURN relationshipType"
                )
            }
        logger.info("库中现有关系类型: %s", sorted(existing_types))

        # 1/2. 外部编码占位（不编造真实编码，待权威数据源对齐后回填）
        step_fill_node_placeholder(driver, "Disease", "icd10", summary)
        step_fill_node_placeholder(driver, "Drug", "atc", summary)

        # 3. 关系属性补齐（默认值含义见模块头部注释）
        for rel_type in TARGET_REL_TYPES:
            if rel_type not in existing_types:
                logger.warning("关系类型 %s 在库中不存在，跳过", rel_type)
                continue
            step_fill_rel_props(driver, rel_type, summary)

        # 4. 疾病患病率 get_prob 写入
        step_write_get_prob(driver, summary)

        # 5. 症状 IDF 写入
        step_write_symptom_idf(driver, summary)
    finally:
        driver.close()

    logger.info("========== 迁移汇总 ==========")
    for step_name, affected in summary:
        logger.info("%-36s 受影响 %d 行", step_name, affected)
    logger.info("迁移完成。所有步骤均为幂等操作，可安全重跑。")


if __name__ == "__main__":
    main()
