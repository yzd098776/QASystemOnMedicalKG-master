# coding: utf-8
"""
召回评测基线（阶段 B0：替换嵌入前必须先跑，否则无法证明升级收益）。

评测集：30 条口语化 query（如「脑子里嗡嗡响」「心跳忽快忽慢」），
每条标注期望实体。分三条路统计：
- 关键词路：别名归一化 + 实体识别 + CONTAINS（services.retriever.keyword_recall）
- 向量路：整句嵌入 + Neo4j 向量索引（services.vector_index.vector_recall）
- 融合路：双路加权（services.retriever.hybrid_anchors）

指标（对每条 query，期望实体命中记 1）：
- Recall@1 ：top1 即命中
- Recall@5 ：前 5 内命中
- MRR      ：首个命中的排名倒数均值

用法（backend 目录，需 Neo4j 可连）：
    python scripts/eval_recall.py                 # 汇总指标
    python scripts/eval_recall.py --check         # 先核对标注实体是否存在于图谱
    python scripts/eval_recall.py --verbose       # 输出逐条命中明细
升级嵌入后同一评测集重跑对比，结果写 README「检索质量评测」一节。
"""

import argparse
import asyncio
import json
import logging
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# 评测集：口语化表述 → 期望实体（图谱中的标准名）。
# 设计原则：优先「字面几乎不重叠」的口语改写（哈希向量只捕捉字面重叠，
# 这类 query 正是语义升级预期受益的场景）。
QUERIES = [
    ("脑子里嗡嗡响，安静下来更明显", ["耳鸣"]),
    ("心跳忽快忽慢，心里发慌", ["心律失常"]),
    ("一换季就连打喷嚏流清水鼻涕", ["鼻炎"]),
    ("嗓子干痒老想清嗓子", ["咽炎"]),
    ("吃完饭胃里烧得慌", ["胃炎"]),
    ("空腹痛吃点东西就缓解", ["消化性溃疡"]),
    ("脸色发白稍微动一动就喘", ["贫血"]),
    ("瘦得很快还怕热出汗多", ["甲亢"]),
    ("大脚趾半夜红肿剧痛", ["痛风"]),
    ("爬两层楼胸口闷痛", ["冠心病"]),
    ("晚上翻来覆去睡不着", ["失眠"]),
    ("什么都不想干高兴不起来", ["抑郁症"]),
    ("总担心要出事坐立不安", ["焦虑症"]),
    ("起夜次数多尿还解不干净", ["前列腺增生"]),
    ("月经周期乱糟糟量还多", ["月经不调"]),
    ("小孩烧退之后出了红疹子", ["幼儿急疹"]),
    ("牙龈老出血身上莫名淤青", ["白血病"]),
    ("口渴总想喝水尿也特别多", ["糖尿病"]),
    ("后脑勺发沉量血压偏高", ["高血压"]),
    ("高烧咳嗽胸口疼还怕冷", ["肺炎"]),
    ("呼气的时候喉咙滋滋响像吹哨", ["哮喘"]),
    ("眼睛发红发痒早上眼屎多", ["结膜炎"]),
    ("耳朵疼听声音像隔了层膜", ["中耳炎"]),
    ("咽口水都疼还发烧嗓子红肿", ["扁桃体炎"]),
    ("早上起来手指头僵硬", ["类风湿关节炎"]),
    ("腰疼连着腿一起发麻", ["腰椎间盘突出"]),
    ("天旋地转的晕还想吐", ["眩晕"]),
    ("好几天不排便肚子发胀", ["便秘"]),
    ("皮肤起红斑脱屑痒得睡不着", ["湿疹"]),
    ("全身酸痛像被人打了一顿还发烧", ["流行性感冒"]),
]


async def eval_paths():
    from services import entity_recognizer
    from services.retriever import keyword_recall, hybrid_anchors
    from services.vector_index import vector_recall

    entity_recognizer.preload()
    results = {"keyword": [], "vector": [], "hybrid": []}
    details = []

    for q, expected in QUERIES:
        exp = set(expected)
        entities = entity_recognizer.recognize(q) or []
        # 关键词路：与 hybrid_anchors 相同的 terms 构造（实体规范名+原词+别名归一化）
        from core.alias import normalize as normalize_alias
        terms = []
        for e in entities:
            for t in (e.get("name"), e.get("raw")):
                if t and t not in terms:
                    terms.append(t)
            norm = normalize_alias(e.get("raw") or "")
            if norm and norm not in terms:
                terms.append(norm)
        if q not in terms:
            terms.append(q)
        kw = await keyword_recall(terms)
        kw_names = [h["name"] for h in kw]
        vr = await vector_recall(q)
        vr_names = [h["name"] for h in vr]
        hy_names = await hybrid_anchors(q, entities)

        for path, names in (("keyword", kw_names), ("vector", vr_names), ("hybrid", hy_names)):
            rank = next((i + 1 for i, n in enumerate(names) if n in exp), 0)
            results[path].append(rank)
        details.append({"query": q, "expected": sorted(exp),
                        "kw_rank": next((i + 1 for i, n in enumerate(kw_names) if n in exp), 0),
                        "vec_rank": next((i + 1 for i, n in enumerate(vr_names) if n in exp), 0),
                        "hyb_rank": next((i + 1 for i, n in enumerate(hy_names) if n in exp), 0)})
    return results, details


def metrics(ranks):
    n = len(ranks)
    r1 = sum(1 for r in ranks if r == 1) / n
    r5 = sum(1 for r in ranks if 0 < r <= 5) / n
    mrr = sum(1.0 / r for r in ranks if r > 0) / n
    miss = sum(1 for r in ranks if r == 0)
    return r1, r5, mrr, miss


async def check_entities():
    """核对标注实体是否存在于图谱（不存在则评测恒 miss，须先修正标注）"""
    from services.graph_db import run_cypher
    all_names = sorted({n for _, exp in QUERIES for n in exp})
    missing = []
    for name in all_names:
        rows = await run_cypher(
            "MATCH (n) WHERE n.name = $name RETURN labels(n)[0] AS label LIMIT 1",
            {"name": name})
        if not rows:
            missing.append(name)
    if missing:
        print("以下标注实体在图谱中不存在，请修正评测集：")
        for m in missing:
            print("  -", m)
        # 给出模糊候选便于改名
        for m in missing:
            rows = await run_cypher(
                "MATCH (n) WHERE n.name CONTAINS $s RETURN n.name AS name LIMIT 5",
                {"s": m[:2]})
            print(f"  「{m}」候选:", [r["name"] for r in rows])
        sys.exit(1)
    print(f"标注实体全部存在于图谱（{len(all_names)} 个）")


def main():
    parser = argparse.ArgumentParser(description="召回评测基线")
    parser.add_argument("--check", action="store_true", help="只核对标注实体存在性")
    parser.add_argument("--verbose", action="store_true", help="输出逐条命中排名")
    parser.add_argument("--json", default=None, help="明细另存 JSON 路径（便于升级前后 diff）")
    args = parser.parse_args()

    if args.check:
        asyncio.run(check_entities())
        return

    async def _run():
        from services.graph_db import close_driver
        try:
            return await eval_paths()
        finally:
            close_driver()

    results, details = asyncio.run(_run())

    print(f"\n评测集 {len(QUERIES)} 条（provider 见 EMBEDDING_PROVIDER）")
    print("路径        Recall@1  Recall@5     MRR   未命中")
    label_map = {"keyword": "关键词路", "vector": "向量路", "hybrid": "融合路"}
    for path in ("keyword", "vector", "hybrid"):
        r1, r5, mrr, miss = metrics(results[path])
        print(f"{label_map[path]:<9}  {r1:>8.2%}  {r5:>8.2%}  {mrr:>7.4f}  {miss:>5}")

    if args.verbose:
        print("\n逐条排名（0=未命中）")
        for d in details:
            print(f"  kw={d['kw_rank']:>2} vec={d['vec_rank']:>2} hyb={d['hyb_rank']:>2}  "
                  f"{d['query']} → {d['expected']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"per_query": details}, f, ensure_ascii=False, indent=2)
        print(f"\n明细已写入 {args.json}")


if __name__ == "__main__":
    main()
