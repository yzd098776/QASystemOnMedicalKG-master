# coding: utf-8
"""
基于词典的医疗实体识别（阶段三 3.1）。

数据源：项目根目录 dict/*.txt（disease/symptom/drug/food/check/department），
每行一个实体词，UTF-8 编码。识别采用正向最大匹配（词长优先），
命中后经 core.alias 别名归一化映射到图谱规范名；
紧邻否认词（如"不/没有/无"，来自 dict/deny.txt）之后的实体跳过，
避免"没有发烧"被误识别为症状陈述。

零 LLM 调用；词典缺失/损坏仅告警降级为空识别结果，不阻断启动。
"""

import logging
import os
from functools import lru_cache

from core.alias import normalize as normalize_alias

logger = logging.getLogger(__name__)

# dict 目录位于项目根目录（backend 的上一级）
_DICT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "dict")
)

# 词典文件 -> 图谱标签映射（deny.txt 为否认词表，单独处理）
_CATEGORY_FILES = {
    "disease.txt": "Disease",
    "symptom.txt": "Symptom",
    "drug.txt": "Drug",
    "food.txt": "Food",
    "check.txt": "Check",
    "department.txt": "Department",
}

# 一个词可能同时出现在多个词典中，按该优先级取首个归属
_CATEGORY_PRIORITY = ["Disease", "Drug", "Symptom", "Check", "Food", "Department"]

# 实体词最小长度：单字误报率高（如"痰""咳"），至少 2 字才视为实体
_MIN_WORD_LEN = 2


@lru_cache(maxsize=1)
def _load_dicts():
    """加载全部词典，返回 (标签->词集合, 最大词长, 否认词集合)。

    编码说明：词典文件为 UTF-8；个别行可能混入非法字节，
    用 errors="ignore" 容错，不影响其余词条加载。
    """
    label_sets = {label: set() for label in _CATEGORY_PRIORITY}
    deny_set = set()
    max_len = 0
    for filename, label in _CATEGORY_FILES.items():
        path = os.path.join(_DICT_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    word = line.strip()
                    if len(word) >= _MIN_WORD_LEN:
                        label_sets[label].add(word)
                        if len(word) > max_len:
                            max_len = len(word)
        except OSError as e:
            logger.warning("实体词典 %s 加载失败（该类实体将无法识别）: %s", filename, e)
    # 否认词表（单字/双字，如 不/没有/无），用于否定上下文过滤
    deny_path = os.path.join(_DICT_DIR, "deny.txt")
    try:
        with open(deny_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                word = line.strip()
                if word:
                    deny_set.add(word)
    except OSError as e:
        logger.warning("否认词典 deny.txt 加载失败（跳过否定过滤）: %s", e)
    total = sum(len(s) for s in label_sets.values())
    logger.info("实体词典加载完成：%d 个实体词，%d 个否认词，最大词长 %d", total, len(deny_set), max_len)
    return label_sets, max(max_len, _MIN_WORD_LEN), deny_set


def _lookup(word: str, label_sets: dict):
    """按优先级返回词所属标签；不在任何词典中返回 None"""
    for label in _CATEGORY_PRIORITY:
        if word in label_sets[label]:
            return label
    return None


def _negated(text: str, start: int, deny_set: set) -> bool:
    """判断 start 位置的实体词前是否紧邻否认词（向前看最多 2 字）"""
    for back in (1, 2):
        if start - back >= 0 and text[start - back:start] in deny_set:
            return True
    return False


def recognize(text: str):
    """在文本中识别医疗实体，返回去重后的实体列表。

    每项结构：{"name": 别名归一化后的规范名, "raw": 原文命中词, "type": 图谱标签}。
    采用正向最大匹配：从当前位置尝试最长可能词长，命中即消费该词。
    """
    if not isinstance(text, str) or not text.strip():
        return []
    label_sets, max_len, deny_set = _load_dicts()
    results = []
    seen = set()
    i, n = 0, len(text)
    while i < n:
        matched = None
        upper = min(max_len, n - i)
        for length in range(upper, _MIN_WORD_LEN - 1, -1):
            word = text[i:i + length]
            label = _lookup(word, label_sets)
            if label is not None:
                matched = (word, label)
                break
        if matched is None:
            i += 1
            continue
        word, label = matched
        # 否认词紧邻其前的实体跳过（如"没有发烧"中的"发烧"）
        if not _negated(text, i, deny_set):
            canonical = normalize_alias(word)
            if canonical not in seen:
                seen.add(canonical)
                results.append({"name": canonical, "raw": word, "type": label})
        i += len(word)
    return results


def preload():
    """启动期显式预热词典，返回实体词总数（供日志/自检使用）"""
    label_sets, _, deny_set = _load_dicts()
    return sum(len(s) for s in label_sets.values()), len(deny_set)
