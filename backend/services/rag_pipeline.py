# coding: utf-8
"""
GraphRAG 问答主管线（阶段三）。

流程（对 /api/chat 的最后一条用户消息）：
1. 急症红牌检测（最高优先级）：命中 → emergency 帧 + 固定引导内容 + [DONE]，
   不进行任何图谱检索与 LLM 调用；
2. 实体识别（词典）+ 意图路由（规则，零 LLM），先输出 intent 帧说明分支；
3. 分支执行：
   - diagnosis:    复用 /api/diagnosis 加权诊断，输出结构化回答 + sources 帧
   - drug_safety:  复用既有药物相互作用逻辑，输出结构化回答
   - graph_lookup: 单实体百科，直接用图谱数据组织回答，不调用 DeepSeek
   - rag:          混合检索三元组（检索不足时可触发一次 Text2Cypher）→
                   注入带 T 编号的上下文 → DeepSeek 流式输出（保留【T#】标记）→
                   内容流结束后输出 sources 帧（被引用三元组 + 句子序号）
4. 无 DEEPSEEK_API_KEY 时 rag 降级为图谱结构化回答，不报错。

SSE 帧协议（既有帧不变，只新增类型）：
- data: {"content": "..."}                      流式文本（既有）
- data: [DONE]                                  结束（既有）
- data: {"emergency": true, "message": ..., "guidance": ...}  急症红牌（最先）
- data: {"intent": "..."}                       意图分支（可选首帧）
- data: {"sources": [...], "has_uncited": bool} 来源三元组（内容流结束后）
"""

import asyncio
import json
import logging
import re

from core.config import DEEPSEEK_API_KEY, TEXT2CYPHER_ENABLED

from . import entity_recognizer, emergency, intent_router, retriever, text2cypher
from .diagnosis_service import run_diagnosis
from .drug_service import run_drug_interaction
from .llm_client import LLMError, stream_chat_completion

logger = logging.getLogger(__name__)

# ========== SSE 帧工具 ==========


def _frame(payload: dict) -> str:
    """把一个 JSON 对象封装为 SSE data 帧（中文不转义，前端按 UTF-8 解析）"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


DONE_FRAME = "data: [DONE]\n\n"


async def _stream_text(text: str, chunk_size: int = 5, delay: float = 0.02):
    """把定稿文本切成 content 帧流式输出（模拟逐字效果，与原降级输出风格一致）"""
    for i in range(0, len(text), chunk_size):
        yield _frame({"content": text[i:i + chunk_size]})
        await asyncio.sleep(delay)


# ========== 句子级溯源（3.5） ==========

# 来源标记形如 【T1】【T12】，允许一句末尾多个
_MARKER_RE = re.compile(r"【T(\d+)】")
# 切句分隔符：中英文句末标点与换行
_SENT_SPLIT_RE = re.compile(r"[。！？!?\n]+")


def parse_source_markers(text: str):
    """解析模型输出中的 【T#】 标记。

    按句号/问号/叹号/换行切句（序号从 1 计），统计每个三元组 id
    被哪些句子引用。返回 (tid -> [句子序号], 是否存在无任何标记的句子)。
    """
    sentences = [s for s in _SENT_SPLIT_RE.split(text or "") if s.strip()]
    cited = {}
    has_uncited = False
    for idx, sent in enumerate(sentences, 1):
        marks = _MARKER_RE.findall(sent)
        if not marks:
            has_uncited = True
            continue
        for m in marks:
            tid = f"T{m}"
            bucket = cited.setdefault(tid, [])
            if idx not in bucket:
                bucket.append(idx)
    return cited, has_uncited


# ========== GraphRAG 提示词（保留免责声明要求） ==========


def _build_rag_system_prompt(triples_context: str, extra_context: str = "") -> str:
    """组装检索增强系统提示词：仅依据图谱三元组回答 + 强制来源标注"""
    parts = [
        "你是一个专业的医疗健康助手。以下是根据用户问题从医疗知识图谱检索到的三元组数据：",
        "",
        triples_context,
        "",
        "回答要求：",
        "1. 仅依据上述图谱三元组回答；上下文中没有的信息必须明确说明不确定，不得编造",
        "2. 引用三元组内容的句子，在句末标注来源标记，格式如【T1】【T3】（允许多个）；"
        "没有三元组依据的句子不要添加标记",
        "3. 语言通俗易懂，避免专业术语堆砌",
        "4. 禁止给出具体用药剂量和手术方案，只提供一般性建议",
        "5. 所有回答末尾必须添加：\"以上内容仅供参考，如有不适请及时就医\"",
        "6. 结合用户提供的健康档案信息给出个性化建议",
    ]
    if extra_context:
        parts += [
            "",
            "补充查询结果（无编号、仅供参考、引用时不要添加来源标记）：",
            extra_context,
        ]
    return "\n".join(parts)


# ========== 实体百科结构化回答（graph_lookup 与无密钥降级共用） ==========

# 关系谓词 -> 中文展示名（方向：out=本实体指向外，in=其他实体指向本实体）
_REL_LABELS = {
    ("has_symptom", "out"): "相关症状",
    ("common_drug", "out"): "常用药品",
    ("do_eat", "out"): "宜吃食物",
    ("no_eat", "out"): "忌吃食物",
    ("recommand_eat", "out"): "推荐食物",
    ("need_check", "out"): "建议检查",
    ("belongs_to", "out"): "所属科室",
    ("acompany_with", "out"): "相关并发症",
    ("has_symptom", "in"): "可能出现该症状的疾病",
    ("common_drug", "in"): "常用该药品的疾病",
    ("need_check", "in"): "需要做该检查的疾病",
    ("do_eat", "in"): "宜吃该食物的疾病",
    ("no_eat", "in"): "忌吃该食物的疾病",
    ("recommand_eat", "in"): "推荐该食物的疾病",
    ("drugs_of", "in"): "生产厂商",
    ("belongs_to", "in"): "相关疾病/科室",
}

# 节点属性 -> 中文展示名（按序展示非空属性）
_PROP_LABELS = (
    ("desc", "简介"),
    ("cause", "病因"),
    ("prevent", "预防措施"),
    ("easy_get", "易感人群"),
    ("cure_lasttime", "治疗周期"),
    ("cured_prob", "治愈概率"),
    ("producer", "生产厂家"),
)


def _build_entity_answer(detail: dict):
    """把实体详情（属性 + 1 跳关系）组织为结构化回答。

    返回 (markdown 文本, sources 三元组列表)；
    关系按谓词分组展示，每个对象生成一条来源三元组（上限 30 条）。
    """
    name = detail["name"]
    props = detail.get("props") or {}
    lines = [f"## {name}", ""]
    for key, label in _PROP_LABELS:
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"**{label}：**{value.strip()}")
            lines.append("")

    # 关系分组：(谓词, 方向) -> [对象名]
    groups = {}
    for rel in detail.get("relations") or []:
        gk = (rel["predicate"], rel["direction"])
        groups.setdefault(gk, []).append(rel["object"])

    sources = []
    if groups:
        lines.append("**图谱关联信息：**")
        for (predicate, direction), objects in groups.items():
            label = _REL_LABELS.get((predicate, direction), predicate)
            shown = objects[:15]
            lines.append(f"- {label}：{'、'.join(shown)}")
            for obj in shown:
                if len(sources) >= 30:
                    break
                if direction == "out":
                    sources.append({
                        "id": f"T{len(sources) + 1}",
                        "subject": name,
                        "predicate": predicate,
                        "object": obj,
                        "sentences": [],
                    })
                else:
                    sources.append({
                        "id": f"T{len(sources) + 1}",
                        "subject": obj,
                        "predicate": predicate,
                        "object": name,
                        "sentences": [],
                    })
    lines.append("")
    lines.append("以上内容仅供参考，如有不适请及时就医")
    return "\n".join(lines), sources


async def _lookup_entity_detail(entity: dict):
    """按实体（规范名优先、原词兜底）查询详情，未命中返回 None"""
    detail = await retriever.fetch_entity_detail(entity["name"])
    if detail is None and entity.get("raw") and entity["raw"] != entity["name"]:
        detail = await retriever.fetch_entity_detail(entity["raw"])
    return detail


# ========== 各意图分支 ==========


async def _diagnosis_branch(question: str, entities):
    """多症状自查：复用 /api/diagnosis 加权诊断（传入原文症状词，
    服务内部会同时纳入别名归一化名与原词）"""
    symptoms = [e["raw"] for e in entities if e["type"] == "Symptom"]
    result = await run_diagnosis(symptoms)
    top = result.get("results") or []

    if not top:
        async for f in _stream_text(
            "暂未在知识图谱中匹配到与您症状相符的疾病，建议及时前往医院就诊，"
            "由医生结合检查结果做出诊断。\n\n以上内容仅供参考，如有不适请及时就医"
        ):
            yield f
        yield DONE_FRAME
        return

    lines = [f"根据您描述的症状（{'、'.join(symptoms)}），按匹配度排序，可能相关的疾病如下：", ""]
    sources = []
    seen = set()
    for i, r in enumerate(top[:5], 1):
        lines.append(f"### {i}. {r['name']}（相对置信度 {r['probability']}%）")
        if r.get("desc") and r["desc"] != "暂无简介":
            lines.append(r["desc"])
            lines.append("")
        lines.append(f"- 匹配症状：{'、'.join(r['matchedSymptoms'])}")
        lines.append(f"- 建议科室：{r['department']}")
        if r.get("checks"):
            lines.append(f"- 建议检查：{'、'.join(r['checks'][:3])}")
        lines.append("")
        # 来源三元组：疾病 -[has_symptom]-> 实际命中症状（从展示名中还原库内命中词）
        for ms in r["matchedSymptoms"]:
            m = re.search(r"（库中命中：([^）]+)）", ms)
            real = m.group(1) if m else ms
            key = (r["name"], real)
            if key in seen:
                continue
            seen.add(key)
            sources.append({
                "id": f"T{len(sources) + 1}",
                "subject": r["name"],
                "predicate": "has_symptom",
                "object": real,
                "sentences": [],
            })
    lines.append("疾病自查结果仅供参考，不能替代医生诊断，如有不适请及时就医。")
    async for f in _stream_text("\n".join(lines)):
        yield f
    if sources:
        yield _frame({"sources": sources})
    yield DONE_FRAME


async def _drug_safety_branch(entities):
    """用药安全：≥2 种药品 → 复用既有药物相互作用逻辑"""
    drugs = [e["name"] for e in entities if e["type"] == "Drug"]
    result = await run_drug_interaction(drugs)
    lines = ["## 用药安全检查", ""]
    for it in result.get("interactions") or []:
        lines.append(f"**{it['drug1']} + {it['drug2']}** —— 风险等级：{it['risk']}")
        lines.append(f"{it['description']}")
        lines.append("")
    lines.append("以上用药组合信息仅供参考，具体请咨询医生或药师。以上内容仅供参考，如有不适请及时就医")
    async for f in _stream_text("\n".join(lines)):
        yield f
    yield DONE_FRAME


async def _graph_lookup_branch(entities):
    """单实体百科：直接用图谱数据组织结构化回答，不调用 DeepSeek"""
    detail = await _lookup_entity_detail(entities[0])
    if detail is None:
        async for f in _stream_text(
            f"知识图谱中暂未找到与「{entities[0]['name']}」相关的信息，"
            "建议尝试更规范的医学名称，或使用知识图谱页面搜索。\n\n"
            "以上内容仅供参考，如有不适请及时就医"
        ):
            yield f
        yield DONE_FRAME
        return
    text, sources = _build_entity_answer(detail)
    async for f in _stream_text(text):
        yield f
    if sources:
        yield _frame({"sources": sources})
    yield DONE_FRAME


async def _fallback_graph_answer(question: str, entities, triples):
    """无 DEEPSEEK_API_KEY 时 rag 意图的降级路径（沿用现有降级风格）：
    优先用首个实体的百科结构化回答，否则枚举检索到的图谱三元组。"""
    if entities:
        detail = await _lookup_entity_detail(entities[0])
        if detail is not None:
            text, sources = _build_entity_answer(detail)
            prefix = "由于AI服务暂未配置API密钥，我基于知识图谱为您提供以下信息：\n\n"
            async for f in _stream_text(prefix + text):
                yield f
            if sources:
                yield _frame({"sources": sources})
            return
    lines = [
        f"您好！您询问的是关于「{question}」的问题。",
        "",
        "由于AI服务暂未配置API密钥，我基于知识图谱为您提供以下相关信息：",
        "",
    ]
    sources = []
    if triples:
        for t in triples[:30]:
            lines.append(f"- {t['subject']} ——[{t['predicate']}]——> {t['object']}")
            sources.append({
                "id": t["id"],
                "subject": t["subject"],
                "predicate": t["predicate"],
                "object": t["object"],
                "sentences": [],
            })
    else:
        lines.append("暂未检索到相关图谱信息，建议尝试更规范的医学名称。")
    lines.append("")
    lines.append("以上内容仅供参考，如有不适请及时就医")
    async for f in _stream_text("\n".join(lines)):
        yield f
    if sources:
        yield _frame({"sources": sources})


async def _rag_branch(llm_messages, question: str, entities):
    """检索增强生成：混合检索三元组 → （可选 Text2Cypher 补位）→
    DeepSeek 流式回答 → 句子级溯源 sources 帧。"""
    anchors = await retriever.hybrid_anchors(question, entities)
    triples = await retriever.fetch_triples(anchors)

    # Text2Cypher 长尾补位：仅在检索结果不足、且开关与密钥均就绪时触发一次；
    # 生成/校验/执行任一环节失败即放弃，回退常规检索增强流程（降级链见 text2cypher 模块）
    extra_context = ""
    if len(triples) < 3 and TEXT2CYPHER_ENABLED and DEEPSEEK_API_KEY:
        logger.info("常规检索三元组不足 %d 条，触发 Text2Cypher 补位", len(triples))
        rows, _info = await text2cypher.text2cypher_search(question)
        if rows:
            extra_context = text2cypher.rows_to_context(rows)

    # 未配置密钥：降级为图谱结构化回答（不报错）
    if not DEEPSEEK_API_KEY:
        async for f in _fallback_graph_answer(question, entities, triples):
            yield f
        yield DONE_FRAME
        return

    triples_context = retriever.triples_to_context(triples) or "（未检索到相关图谱三元组，请明确告知用户信息不足）"
    system_prompt = _build_rag_system_prompt(triples_context, extra_context)
    # llm_messages[0] 为路由层组装的通用系统提示词，此处替换为带上下文的 GraphRAG 提示词，
    # 其余消息（健康档案/用户上下文/对话历史）原样保留
    messages = [{"role": "system", "content": system_prompt}] + list(llm_messages[1:])

    parts = []
    try:
        async for chunk in stream_chat_completion(messages):
            parts.append(chunk)
            # 内容帧照常输出，【T#】标记保留原文，由前端渲染为角标
            yield _frame({"content": chunk})
    except LLMError as e:
        logger.error("DeepSeek 调用失败: %s", e)
        if not parts:
            yield _frame({"content": "AI服务暂时不可用，请稍后再试。"})
            yield DONE_FRAME
            return

    full_text = "".join(parts)
    # 句子级溯源（3.5）：解析标记，输出被引用三元组及句子序号
    if triples and full_text:
        cited, has_uncited = parse_source_markers(full_text)
        sources = []
        for t in triples:
            if t["id"] in cited:
                sources.append({
                    "id": t["id"],
                    "subject": t["subject"],
                    "predicate": t["predicate"],
                    "object": t["object"],
                    "sentences": cited[t["id"]],
                })
        if sources:
            yield _frame({"sources": sources, "has_uncited": has_uncited})
    yield DONE_FRAME


# ========== 管线入口 ==========


async def run_graphrag_chat(llm_messages, question: str):
    """GraphRAG 问答管线入口（异步生成器，逐帧产出 SSE 字符串）。

    llm_messages: 路由层组装好的消息列表（[0] 为通用系统提示词，
                  后续为健康档案/用户上下文/对话历史）；
    question:     最后一条用户消息文本（急症检测与实体识别对象）。
    """
    # 1. 急症红牌（最前置）：命中且非纯定义性提问 → 红牌 + 固定引导，不做检索与 LLM 调用；
    #    「什么是胸痛/休克的含义」这类概念查询豁免红牌、交回意图路由（见 emergency.is_definition_query）
    em = emergency.check_emergency(question)
    if em is not None and not emergency.is_definition_query(question):
        logger.info("急症红牌命中（关键词: %s），终止问答流程", em["matched"])
        yield _frame({"emergency": True, "message": em["message"], "guidance": em["guidance"]})
        async for f in _stream_text(emergency.EMERGENCY_CONTENT):
            yield f
        yield DONE_FRAME
        return

    # 2. 实体识别 + 意图路由（零 LLM 调用）
    try:
        entities = entity_recognizer.recognize(question)
        intent = intent_router.route_intent(question, entities)
    except Exception as e:
        # 识别/路由异常不阻断问答：兜底走 rag 分支
        logger.error("意图路由异常，兜底走检索增强: %s", e)
        entities, intent = [], "rag"
    logger.info(
        "意图路由: intent=%s, entities=%s",
        intent, [(e["name"], e["type"]) for e in entities],
    )
    # intent 帧：可选首帧，告知前端本次回答走的分支
    yield _frame({"intent": intent})

    # 3. 分支执行
    try:
        if intent == "diagnosis":
            branch = _diagnosis_branch(question, entities)
        elif intent == "drug_safety":
            branch = _drug_safety_branch(entities)
        elif intent == "graph_lookup":
            branch = _graph_lookup_branch(entities)
        else:
            branch = _rag_branch(llm_messages, question, entities)
        async for f in branch:
            yield f
    except Exception as e:
        logger.error("问答管线分支执行异常: %s", e)
        yield _frame({"content": "服务暂时不可用，请稍后再试。"})
        yield DONE_FRAME
