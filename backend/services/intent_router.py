# coding: utf-8
"""
意图路由（阶段三 3.1）：规则 + 词典优先，零 LLM 调用。

输入：用户问题文本 + entity_recognizer 的识别结果；
输出：意图标签之一：

- emergency:    由管线最前置的 emergency.check_emergency 判定（不在本模块）
- drug_safety:  识别出 ≥2 种药品 → 复用既有药物相互作用逻辑
- diagnosis:    多症状自查表述（≥2 个症状，或 1 个症状 + 强自查措辞）
                → 复用 /api/diagnosis 的加权匹配核心逻辑
- graph_lookup: 识别出实体且命中百科式措辞（什么是/症状/预防/禁忌...）
                → 直接用图谱数据组织结构化回答，不调用 DeepSeek
- rag:          其余问题 → 检索增强 + DeepSeek（未配置密钥时降级为
                图谱结构化回答，详见 rag_pipeline）
"""

# 强自查措辞：仅识别出 1 个症状时，命中这些表述也走诊断分支
_SELF_CHECK_PATTERNS = (
    "可能是什么",
    "是什么病",
    "什么病",
    "可能得了",
    "是不是得了",
    "得了什么",
    "自查",
    "怎么回事",
    "什么原因",
    "什么情况",
)

# 百科式措辞：识别出实体且命中这些表述时，走单实体百科（图谱结构化回答）
_ENCYCLOPEDIA_PATTERNS = (
    "什么是",
    "是什么",
    "介绍一下",
    "介绍下",
    "是什么病",
    "病因",
    "怎么引起",
    "如何引起",
    "症状",
    "怎么治",
    "如何治",
    "治疗方法",
    "治疗",
    "预防",
    "禁忌",
    "忌口",
    "吃什么药",
    "用什么药",
    "吃什么好",
    "挂什么科",
    "看什么科",
    "做什么检查",
    "要做什么检查",
    "严重吗",
    "传染吗",
    "能治好吗",
    "吃什么",
    "不能吃什么",
)


def is_self_check(question: str) -> bool:
    """是否命中强自查措辞"""
    return any(p in question for p in _SELF_CHECK_PATTERNS)


def is_encyclopedia(question: str) -> bool:
    """是否命中百科式措辞"""
    return any(p in question for p in _ENCYCLOPEDIA_PATTERNS)


def route_intent(question: str, entities) -> str:
    """根据实体识别结果与措辞规则路由意图（零 LLM 调用）。

    优先级：drug_safety（≥2 药品）> diagnosis（多症状自查）
    > graph_lookup（实体 + 百科措辞）> rag（兜底）。
    """
    if not question or not question.strip():
        return "rag"

    drugs = [e for e in entities if e["type"] == "Drug"]
    symptoms = [e for e in entities if e["type"] == "Symptom"]

    # 用药安全：两种及以上药品 → 药物相互作用检查（复用既有药物逻辑）
    if len(drugs) >= 2:
        return "drug_safety"

    # 疾病自查：≥2 个症状，或 1 个症状 + 强自查措辞
    if len(symptoms) >= 2 or (len(symptoms) >= 1 and is_self_check(question)):
        return "diagnosis"

    # 单实体百科：识别出任一医疗实体且命中百科措辞（不调用 DeepSeek）
    if entities and is_encyclopedia(question):
        return "graph_lookup"

    # 其余走检索增强生成（未配置密钥时在管线内降级为图谱结构化回答）
    return "rag"
