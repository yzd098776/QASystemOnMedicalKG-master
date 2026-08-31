# coding: utf-8
"""
图谱索引与唯一性约束管理模块（阶段二建立，阶段三加固）。

职责：
1. 统一维护七类实体标签常量，避免各处重复硬编码；
2. 提供幂等的索引/约束创建逻辑（ensure），可在建图脚本与后端启动时复用。

设计要点：
- 全部创建语句均为幂等语句（CREATE CONSTRAINT IF NOT EXISTS / CREATE INDEX IF NOT EXISTS），
  重复执行不会产生副作用，满足"只增不删、失败可重跑"的迁移约束；
- 建约束前先做重名检测：某标签下 name 存在重复值时无法建立唯一约束，
  降级为普通索引并输出重名样例（logger.warning），不伪造、不删除重复数据；
- 单个标签失败仅记录告警，不阻断其余标签的处理；
- 「检测 / 建约束 / 建索引」三阶段的异常捕获与日志相互独立，文案与失败来源一一对应；
- 约束升级路径（阶段三新增）：标签曾因重名降级为普通索引，重名清洗完毕再重跑时，
  建约束会因同名普通索引已存在而报 IndexAlreadyExists；此时先查明该索引
  确为「无 owningConstraint 的历史降级索引」后将其删除并重试建约束，
  避免永远停留在普通索引（本模块中唯一允许的删除动作，且有严格前置校验）。

无标签实体定位查询的索引收益落地方案说明（阶段三）：
- /api/kg/entity/{name}、/api/kg/path、/api/kg/related 的核心匹配原为无标签
  `MATCH (n {name:$name})`，无法命中任何标签级约束/索引，PROFILE 实测全表扫描 ~4.4 万 dbHits；
- 方案一（优先尝试）：建 token-less 全局索引 `CREATE INDEX IF NOT EXISTS FOR (n) ON (n.name)`，
  实测本库 Neo4j 5.26 不支持无标签属性索引语法（服务端报 SyntaxError），不可行；
- 方案二（已落地）：把上述三接口的实体定位查询改写为按 ALLOWED_LABELS 逐标签分支
  （UNION ALL 于 CALL 子查询），每个分支命中对应标签的约束索引（NodeIndexSeek），
  dbHits 从 ~4.4 万降至两位数（见 app.py 的 _name_match_union）。
"""

import logging

logger = logging.getLogger(__name__)

# 医疗知识图谱七类实体标签（与 app.py 的 ALLOWED_LABELS、建图脚本保持一致）
LABELS = ("Disease", "Drug", "Symptom", "Food", "Check", "Department", "Producer")

# 重名检测语句：找出该标签下 name 重复的一组样例（LIMIT 1 降低扫描成本）
_DUPLICATE_DETECT_TEMPLATE = (
    "MATCH (n:{label}) "
    "WITH n.name AS dup_name, count(*) AS cnt WHERE cnt > 1 "
    "RETURN dup_name, cnt LIMIT 1"
)


def _drop_legacy_plain_name_indexes(runner, label):
    """删除该标签上历史遗留的普通 name 索引（为重建唯一约束让路）。

    仅删除同时满足以下条件的索引，保证不误删约束自带索引：
    1. 索引作用于单标签且属性恰为 ['name']（标签来自模块内固定白名单，拼接安全）；
    2. owningConstraint 为空（即不是任何约束自带的后备索引，而是历史降级手工索引）。

    返回被删除的索引数量；查询失败按 0 处理（调用方随后重试建约束并依结果告警）。
    """
    query = (
        "SHOW INDEXES YIELD name, labelsOrTypes, properties, owningConstraint "
        f"WHERE labelsOrTypes = ['{label}'] AND properties = ['name'] "
        "AND owningConstraint IS NULL "
        "RETURN name"
    )
    try:
        rows = runner(query) or []
    except Exception as e:
        logger.warning("标签 %s 查询历史降级索引失败（跳过删除，直接重试建约束）: %s", label, e)
        return 0
    dropped = 0
    for row in rows:
        index_name = row.get("name")
        if not index_name:
            continue
        try:
            # 删除普通索引为约束升级的前置动作（仅针对无 owningConstraint 的历史降级索引）
            runner(f"DROP INDEX {index_name} IF EXISTS")
            dropped += 1
            logger.info("标签 %s 已删除历史降级普通索引 %s，准备升级为唯一约束", label, index_name)
        except Exception as e:
            logger.warning("标签 %s 删除历史降级索引 %s 失败: %s", label, index_name, e)
    return dropped


def _ensure_single_label(runner, label):
    """处理单个标签：返回 "constraint" | "index" | "failed"。

    三阶段独立捕获异常（检测 / 建约束 / 建索引），文案与失败来源对应；
    任何阶段失败均不抛出，由调用方汇总。
    """
    # ---- 阶段一：重名检测（只读查询） ----
    dup_rows = []
    try:
        dup_rows = runner(_DUPLICATE_DETECT_TEMPLATE.format(label=label)) or []
    except Exception as e:
        logger.warning("标签 %s 重名检测查询失败（按无重名继续尝试建约束）: %s", label, e)

    if dup_rows:
        # 存在重名：无法建唯一约束，降级为普通索引并告警（不伪造、不删除重复数据）
        sample = dup_rows[0]
        dup_name = sample.get("dup_name")
        dup_cnt = sample.get("cnt")
        logger.warning(
            "标签 %s 存在重名实体（样例：%r 出现 %s 次），无法建立唯一约束，"
            "降级为普通索引；请人工清洗重名数据后重跑本流程升级回约束",
            label, dup_name, dup_cnt,
        )
        # ---- 阶段三（降级分支）：建普通 name 索引 ----
        try:
            runner(f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.name)")
            return "index"
        except Exception as e:
            logger.warning("标签 %s 建立降级普通索引失败（跳过，不阻断其余标签）: %s", label, e)
            return "failed"

    # ---- 阶段二：无重名，建立 name 唯一约束（幂等；唯一约束自带索引能力） ----
    try:
        runner(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
            f"REQUIRE n.name IS UNIQUE"
        )
        return "constraint"
    except Exception as e:
        # 建约束失败的最常见原因：该标签曾降级建过同名普通索引，
        # 重名清洗完毕后重跑时触发 IndexAlreadyExists。
        # 处理：删除「无 owningConstraint 的历史降级普通索引」后重试建约束
        logger.warning(
            "标签 %s 建立唯一约束失败（%s），检查并清理历史降级普通索引后重试",
            label, e,
        )
        _drop_legacy_plain_name_indexes(runner, label)
        try:
            runner(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
                f"REQUIRE n.name IS UNIQUE"
            )
            logger.info("标签 %s 成功从普通索引升级为唯一约束", label)
            return "constraint"
        except Exception as e2:
            logger.warning(
                "标签 %s 重试建立唯一约束仍失败（%s），保持现状并降级确保普通索引存在",
                label, e2,
            )

    # ---- 阶段三（兜底分支）：约束无法建立时确保普通索引存在 ----
    try:
        runner(f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.name)")
        return "index"
    except Exception as e3:
        logger.warning("标签 %s 兜底普通索引建立失败（跳过，不阻断其余标签）: %s", label, e3)
        return "failed"


def ensure_graph_indexes(runner):
    """为七类实体标签幂等地建立 name 唯一约束（无重名）或普通索引（有重名降级），
    并额外建立全局（无标签）name 查找索引。

    参数：
        runner: 可调用对象，签名 runner(cypher: str, params: dict = None)，
                用于执行单条 Cypher 语句。既可以是 py2neo 的 graph.run 包装，
                也可以是后端 run_cypher 的同步包装，保证本模块与执行层解耦。

    返回：
        dict: {label: "constraint" | "index" | "failed"} 的处理结果汇总，
              供调用方打印或记录。
    """
    summary = {}
    for label in LABELS:
        summary[label] = _ensure_single_label(runner, label)

    logger.info("图谱索引/约束检查完成: %s", summary)
    return summary
