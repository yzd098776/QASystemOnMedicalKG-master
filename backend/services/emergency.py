# coding: utf-8
"""
急症红牌检测（阶段三 3.6）。

在 /api/chat 最前置位置对最后一条用户消息做子串匹配检测：
命中 backend/data/red_flags.json 中任一关键词，管线立即发出
emergency 帧与固定急诊引导内容，直接结束，不进行图谱检索与任何
LLM 调用，保证急症提示零延迟、零依赖。

关键词表可在 red_flags.json 中扩展（详见该文件 _comment 字段）。
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

# 红牌表位于 backend/data/red_flags.json（本模块位于 backend/services/ 下）
_RED_FLAGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "red_flags.json",
)


@lru_cache(maxsize=1)
def _load_red_flags() -> dict:
    """加载红牌关键词表；文件缺失/损坏时返回空表并告警（降级为不触发急症帧）"""
    try:
        with open(_RED_FLAGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        patterns = [str(p).strip() for p in data.get("patterns", []) if str(p).strip()]
        result = {
            "patterns": patterns,
            "message": data.get("message", "检测到可能的急症信号，请立即就医！"),
            "guidance": data.get("guidance", "请立即拨打 120 或前往最近的医院急诊科就诊。"),
        }
        logger.info("急症红牌关键词表加载完成，共 %d 个关键词", len(patterns))
        return result
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("急症红牌关键词表加载失败（急症检测降级为不触发）: %s", e)
        return {"patterns": [], "message": "", "guidance": ""}


def check_emergency(text: str):
    """检测文本是否命中急症关键词。

    命中返回 {"matched": 命中词, "message": ..., "guidance": ...}；
    未命中或输入为空返回 None。
    """
    if not isinstance(text, str) or not text:
        return None
    flags = _load_red_flags()
    for pattern in flags["patterns"]:
        if pattern in text:
            return {
                "matched": pattern,
                "message": flags["message"],
                "guidance": flags["guidance"],
            }
    return None


# ========== 定义性提问豁免（降低红牌误报） ==========
# 强定义句式：用户在「询问某概念是什么」而非「自述当下症状」时的典型措辞
_DEF_PATTERNS = (
    "什么是", "是什么", "啥是", "什么意思", "的含义", "的定义",
    "怎么理解", "如何理解", "介绍一下", "介绍下", "解释一下", "解释下",
    "包括哪些", "有哪些", "怎么定义",
)
# 出现任一即时认定为真实自述/紧急情况，绝不豁免（安全优先，宁可触发红牌）
_URGENT_MARKERS = (
    "我", "俺", "本人", "突然", "忽然", "现在", "刚才", "马上", "赶紧",
    "急救", "120", "晕倒", "倒地", "出血", "流了", "疼得", "痛得", "喘不上",
    "喘不过", "救救", "孩子", "宝宝", "老人", "家里", "家人", "他", "她",
)


def is_definition_query(text: str) -> bool:
    """判断文本是否为「概念性定义提问」（如『什么是胸痛』『休克的含义』）。

    用于在急症红牌检测命中后做一次豁免：这类提问是知识查询而非症状自述，
    直接转急诊会形成误报打扰。判据刻意保守——必须命中强定义句式，
    且不含任何第一人称/急迫描述词（_URGENT_MARKERS），才认定为定义提问；
    任何存疑一律不豁免，保证真急症不被漏判。
    """
    if not isinstance(text, str) or not text.strip():
        return False
    if any(u in text for u in _URGENT_MARKERS):
        return False
    return any(p in text for p in _DEF_PATTERNS)


# 急症固定引导正文（随 emergency 帧之后以 content 帧流式输出，含免责声明）
EMERGENCY_CONTENT = (
    "## ⚠️ 急症就医指引\n\n"
    "您描述的情况可能属于急症，**请立即采取行动，不要等待线上回复**：\n\n"
    "1. **立即拨打 120** 急救电话，或请身边的人马上送您前往最近的医院急诊科；\n"
    "2. 等待救援期间保持静卧、避免活动，不要自行驾车就医；\n"
    "3. 如既往有医生开具的急救药品（如硝酸甘油等），请遵医嘱使用；\n"
    "4. 请让身边人陪同并协助呼救，保持通讯畅通。\n\n"
    "以上内容仅供参考，本系统不能替代急救服务与医生诊断，如有不适请及时就医。"
)


def preload() -> int:
    """启动期预热红牌表，返回关键词条数"""
    return len(_load_red_flags()["patterns"])
