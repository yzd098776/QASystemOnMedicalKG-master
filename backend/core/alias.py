# coding: utf-8
"""
实体别名（同义词）归一化模块（阶段二）。

职责：把口语化/俗称的实体词归一化为图谱中的规范实体名，提升
实体查询、诊断自查等接口的召回率。

词典说明（backend/data/aliases.json，人工审校并逐一查库确认两端实体）：
- 规范名一端均为图谱中真实存在的实体；
- 「发烧→发热」：图谱症状节点实际使用「发烧」，无独立「发热」症状节点，
  收录该映射是为对齐医学规范词；调用方均带"归一化名未命中原词兜底再查"，
  不会造成 0 召回；
- 「感冒→上呼吸道感染」：两者均为图谱中独立存在的疾病实体，映射按任务规范
  收录；查询「感冒」时优先命中「上呼吸道感染」；
- 「新冠/新冠肺炎→冠状病毒感染」：图谱无「新型冠状病毒肺炎」实体，
  按库中实际存在的最近语义实体「冠状病毒感染」映射；
- 「头晕」本身即图谱规范症状名，无需收录；「夜间阵发性呼吸困难」在库中
  以「阵发性夜间呼吸困难」存在，两者语义等同仅词序不同，收录为词序归一化映射；
- 药品类映射（阶段三新增，均查库确认规范名端实体存在、别名端无同名节点）：
  「扑热息痛→对乙酰氨基酚片」「泰诺→对乙酰氨基酚缓释片」（对乙酰氨基酚的
  口语名/品牌名，库中无裸名「对乙酰氨基酚」节点，映射到其常见剂型实体）、
  「芬必得→布洛芬缓释胶囊」（布洛芬缓释制剂品牌名）、「阿司匹林→阿司匹林肠溶片」
  （库中无裸名「阿司匹林」节点，映射到最常见剂型实体）；

加载策略：启动时由 app.py lifespan 调用 preload_alias_map() 一次性加载入内存；
加载失败仅告警并降级为"原样返回"，不阻断启动。
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

# 词典路径：backend/data/aliases.json（本模块位于 backend/core/ 下）
_ALIASES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "aliases.json",
)


@lru_cache(maxsize=1)
def _load_alias_map() -> dict:
    """加载并缓存别名词典；文件缺失/损坏时返回空表并告警（降级为原样返回）"""
    try:
        with open(_ALIASES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("aliases.json 顶层必须是 别名->规范名 的对象")
        alias_map = {}
        for alias, canonical in data.items():
            key = str(alias).strip()
            value = str(canonical).strip()
            if key and value:
                alias_map[key] = value
        logger.info("别名词典加载完成，共 %d 条映射", len(alias_map))
        return alias_map
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("别名词典加载失败，归一化降级为原样返回: %s", e)
        return {}


def preload_alias_map() -> int:
    """启动期显式预热词典，返回映射条数（供日志/自检使用）"""
    return len(_load_alias_map())


def normalize(name):
    """把输入词归一化为规范实体名；无映射、非字符串或空值时原样返回。

    调用方约定：归一化名查询未命中时，需用原词再查一次兜底，
    避免词典偏差导致 0 召回。
    """
    if not isinstance(name, str) or not name:
        return name
    return _load_alias_map().get(name.strip(), name)
