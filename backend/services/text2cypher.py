# coding: utf-8
"""
Text2Cypher 长尾覆盖（阶段三 3.4，带白名单校验）。

启用条件：配置了 DEEPSEEK_API_KEY 且 TEXT2CYPHER_ENABLED=true，
且常规检索（3.2/3.3）三元组结果不足时由管线触发一次。

安全原则——绝不信任模型输出：
1. 去除注释后做关键字校验：出现 CREATE/MERGE/DELETE/DETACH/SET/REMOVE/
   DROP/CALL/FOREACH 等写操作或过程调用、分号/多语句一律拒绝；
   必须包含 MATCH 与 RETURN；
2. 无 LIMIT 时自动追加 LIMIT 50，结果行数上限 50；
3. 执行走只读事务 + 事务超时（TEXT2CYPHER_TIMEOUT 秒）。

降级链：生成失败 / 校验失败 / 执行失败或超时 → 放弃 Text2Cypher，
回退 3.2 常规检索增强流程（由 rag_pipeline 捕获并记录日志）。
"""

import logging
import re

from core.config import TEXT2CYPHER_TIMEOUT

from .graph_db import run_readonly
from .llm_client import complete_chat

logger = logging.getLogger(__name__)

# 命中即拒绝的关键字（写操作/结构变更/过程调用/事务控制等），按整词匹配
_FORBIDDEN_KEYWORDS = (
    "CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP",
    "CALL", "FOREACH", "LOAD", "START", "GRANT", "DENY", "REVOKE",
    "USE", "TERMINATE", "PERIODIC", "COMMIT", "CONSTRAINT", "INDEX",
)

# 允许出现的子句关键字（校验提示与注释用；实际拦截以黑名单 + 必备子句实现）
ALLOWED_CLAUSES = (
    "MATCH", "OPTIONAL", "WHERE", "WITH", "RETURN",
    "ORDER", "BY", "SKIP", "LIMIT", "DISTINCT", "AS", "UNION",
)

# 结果行数上限
MAX_ROWS = 50

_GEN_SYSTEM = (
    "你是 Neo4j Cypher 查询专家。医疗知识图谱的节点标签有："
    "Disease（疾病）、Drug（药品）、Symptom（症状）、Food（食物）、"
    "Check（检查）、Department（科室）、Producer（药厂）；"
    "常见关系：has_symptom（有症状）、common_drug（常用药）、do_eat（宜吃）、"
    "no_eat（忌吃）、need_check（需检查）、belongs_to（属于科室）、"
    "acompany_with（并发症）、drugs_of（生产）。"
    "节点常用属性：name、desc、cause、prevent、easy_get、cure_lasttime、cured_prob。"
    "你只能生成一条只读查询（仅允许 MATCH / OPTIONAL MATCH / WHERE / WITH / "
    "RETURN / ORDER BY / SKIP / LIMIT / DISTINCT / AS / UNION），"
    "禁止任何写操作与 CALL 过程，禁止分号与多语句，必须带 LIMIT（不超过 50）。"
    "只输出 Cypher 语句本身，不要解释、不要代码块。"
)


def strip_comments(query: str) -> str:
    """去除 Cypher 注释（// 行注释与 /* */ 块注释），防止注释藏匿恶意语句"""
    query = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    query = re.sub(r"//[^\n]*", " ", query)
    return query


def validate_cypher(query: str):
    """白名单校验模型生成的 Cypher，返回 (是否通过, 校验后的语句或拒绝原因)。

    规则（全部通过才放行）：
    - 非空；
    - 去注释后不得含分号（多语句一律拒绝）；
    - 整词匹配不得出现 _FORBIDDEN_KEYWORDS 中任何关键字（大小写不敏感）；
    - 必须包含 MATCH 与 RETURN 子句；
    - 无 LIMIT 时自动追加 LIMIT 50。
    """
    if not isinstance(query, str) or not query.strip():
        return False, "生成结果为空"
    cleaned = strip_comments(query).strip().rstrip(";").strip()
    if not cleaned:
        return False, "去注释后为空"
    if ";" in cleaned:
        return False, "包含分号/多语句，已拒绝"
    # 整词匹配禁用关键字（\b 边界，大小写不敏感）
    upper = cleaned.upper()
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(r"\b" + kw + r"\b", upper):
            return False, f"包含禁用关键字 {kw}，已拒绝"
    if not re.search(r"\bMATCH\b", upper):
        return False, "缺少 MATCH 子句，已拒绝"
    if not re.search(r"\bRETURN\b", upper):
        return False, "缺少 RETURN 子句，已拒绝"
    if not re.search(r"\bLIMIT\s+\d+", upper):
        cleaned = cleaned + "\nLIMIT 50"
    return True, cleaned


def _extract_cypher(raw: str) -> str:
    """从模型输出中剥出 Cypher 本体（去掉可能的 ``` 代码块围栏）"""
    text = (raw or "").strip()
    if text.startswith("```"):
        # 去掉首行围栏（可能带语言标记）与末尾围栏
        lines = text.split("\n")
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def generate_cypher(question: str):
    """调用 LLM 生成只读 Cypher（低温度，要求确定性输出），返回原始文本"""
    messages = [
        {"role": "system", "content": _GEN_SYSTEM},
        {"role": "user", "content": f"用户问题：{question}\n请生成查询该问题所需信息的只读 Cypher。"},
    ]
    return await complete_chat(messages, temperature=0.1, max_tokens=500)


async def text2cypher_search(question: str):
    """完整 Text2Cypher 链路：生成 → 校验 → 只读超时执行。

    成功返回 (rows, cypher)（rows ≤ 50 行）；任何环节失败返回 (None, 原因)。
    调用方（rag_pipeline）收到 None 时降级回 3.2 常规检索流程。
    """
    try:
        raw = await generate_cypher(question)
    except Exception as e:
        logger.warning("Text2Cypher 生成失败，降级回常规检索: %s", e)
        return None, f"生成失败: {e}"
    cypher_raw = _extract_cypher(raw)
    ok, result = validate_cypher(cypher_raw)
    if not ok:
        logger.warning("Text2Cypher 校验拒绝（%s），原句: %.200s", result, cypher_raw)
        return None, result
    try:
        rows = await run_readonly(result, timeout=TEXT2CYPHER_TIMEOUT)
    except Exception as e:
        logger.warning("Text2Cypher 执行失败/超时，降级回常规检索: %s", e)
        return None, f"执行失败: {e}"
    rows = (rows or [])[:MAX_ROWS]
    logger.info("Text2Cypher 执行成功，返回 %d 行: %.200s", len(rows), result)
    return rows, result


def rows_to_context(rows) -> str:
    """把 Text2Cypher 结果行渲染为注入提示词的辅助上下文（无 T 编号、不可被引用）"""
    lines = []
    for i, row in enumerate(rows[:MAX_ROWS], 1):
        fields = "; ".join(f"{k}={v}" for k, v in row.items() if v is not None)
        lines.append(f"{i}. {fields}")
    return "\n".join(lines)
