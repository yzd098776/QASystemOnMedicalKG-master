# coding: utf-8
"""
图谱索引与唯一性约束管理模块（阶段二）。

职责：
1. 统一维护七类实体标签常量，避免各处重复硬编码；
2. 提供幂等的索引/约束创建逻辑（ensure），可在建图脚本与后端启动时复用。

设计要点：
- 全部语句均为幂等语句（CREATE CONSTRAINT IF NOT EXISTS / CREATE INDEX IF NOT EXISTS），
  重复执行不会产生副作用，满足"只增不删、失败可重跑"的迁移约束；
- 建约束前先做重名检测：某标签下 name 存在重复值时无法建立唯一约束，
  降级为普通索引并输出重名样例（logger.warning），不伪造、不删除重复数据；
- 单个标签失败仅记录告警，不阻断其余标签的处理。
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


def ensure_graph_indexes(runner):
    """为七类实体标签幂等地建立 name 唯一约束（无重名）或普通索引（有重名降级）。

    参数：
        runner: 可调用对象，签名 runner(cypher: str, params: dict = None)，
                用于执行单条 Cypher 语句。既可以是 py2neo 的 graph.run 包装，
                也可以是后端 run_cypher 的同步包装，保证本模块与执行层解耦。

    返回：
        dict: {label: "constraint" | "index" | "skipped" | "failed"} 的处理结果汇总，
              供调用方打印或记录。
    """
    summary = {}
    for label in LABELS:
        try:
            # 第一步：重名检测（只读查询，失败按"未知"处理时仍尝试建约束并兜底）
            dup_rows = runner(_DUPLICATE_DETECT_TEMPLATE.format(label=label)) or []
            if dup_rows:
                sample = dup_rows[0]
                dup_name = sample.get("dup_name")
                dup_cnt = sample.get("cnt")
                logger.warning(
                    "标签 %s 存在重名实体（样例：%r 出现 %s 次），无法建立唯一约束，"
                    "降级为普通索引；请人工清洗重名数据后重跑本流程升级回约束",
                    label, dup_name, dup_cnt,
                )
                # 降级方案：普通 name 索引（幂等）
                runner(
                    f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.name)"
                )
                summary[label] = "index"
            else:
                # 无重名：建立 name 唯一约束（幂等；唯一约束自带索引能力）
                runner(
                    f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
                    f"REQUIRE n.name IS UNIQUE"
                )
                summary[label] = "constraint"
        except Exception as e:
            # 兜底：唯一约束可能因其他原因失败（如约束与既有索引冲突），
            # 再尝试一次普通索引；仍失败则记录告警并跳过该标签，不阻断其余标签
            logger.warning("标签 %s 建立唯一约束失败，尝试降级普通索引: %s", label, e)
            try:
                runner(f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.name)")
                summary[label] = "index"
            except Exception as e2:
                logger.warning("标签 %s 索引建立失败（跳过，不阻断其余标签）: %s", label, e2)
                summary[label] = "failed"
    logger.info("图谱索引/约束检查完成: %s", summary)
    return summary
