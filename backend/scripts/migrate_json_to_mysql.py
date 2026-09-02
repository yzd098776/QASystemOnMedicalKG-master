# coding: utf-8
"""
五处 JSON → MySQL 迁移脚本（阶段 A）。

特性（对齐强制约束 6「数据零丢失」）：
- 幂等可重跑：以「库现状 vs JSON」做 diff 增量同步，重复执行结果收敛、
  不产生重复行；
- 外键顺序：先 users 主表，再四张子表（profiles/records/plans/chat）；
  子表中不存在于 users 的孤儿键会被拦截报告（FK 约束保护）；
- 逐表计数校验 + 随机抽样逐字段比对（默认 10 条，可调）；
- 原 JSON 复制为同目录 .json.bak（已存在则保留首份，不删除任何文件）；
- --dry-run 只报告将发生的动作，不写库。

用法（backend 目录下）：
    python scripts/migrate_json_to_mysql.py --dry-run
    python scripts/migrate_json_to_mysql.py
    python scripts/migrate_json_to_mysql.py --sample 20
"""

import argparse
import json
import logging
import os
import random
import shutil
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate_json_to_mysql")

# (逻辑名, JSON 文件, 表属性名)；顺序即迁移顺序（users 最先，子表按外键依赖）
STORES = [
    ("users", "users.json", "users"),
    ("profiles", "profiles.json", "profiles"),
    ("health_records", "health_records.json", "health_records"),
    ("health_plans", "health_plans.json", "health_plans"),
    ("chat_history", "chat_history.json", "chat_history"),
]


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def canon(value):
    """比对用规范化：经 JSON 往返消除 dict/list 类型差异（MySQL JSON 列往返后
    字符串仍为字符串、数字仍为数字，键序不参与比较）"""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="五处 JSON → MySQL 幂等迁移")
    parser.add_argument("--dry-run", action="store_true",
                        help="只报告将发生的动作，不写库")
    parser.add_argument("--sample", type=int, default=10,
                        help="每表抽样比对条数（默认 10）")
    args = parser.parse_args()

    # 延迟导入：core.config 会加载 .env 并校验（sql 模式要求 MYSQL_PASSWORD）
    from core import db
    from core.config import MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE, MYSQL_USER, STORE_BACKEND
    if STORE_BACKEND != "sql":
        logger.warning(
            "当前 STORE_BACKEND=%s（脚本仍按 MYSQL_* 参数直连迁移，不受影响）；"
            "迁移验证通过后请在 backend/.env 设 STORE_BACKEND=json→sql 切换运行时后端",
            STORE_BACKEND)

    logger.info("连接 MySQL %s:%s/%s（用户 %s）", MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE, MYSQL_USER)

    # 1. 读五处 JSON
    sources = {}
    for name, fname, _ in STORES:
        path = os.path.join(_BACKEND_DIR, fname)
        sources[name] = load_json(path)
        logger.info("读取 %-14s %3d 条  %s", name, len(sources[name]), path)

    # 孤儿键预检（子表键必须存在于 users，FK 约束）
    user_keys = set(sources["users"])
    orphan_total = 0
    for name, _, _ in STORES[1:]:
        orphans = sorted(set(sources[name]) - user_keys)
        if orphans:
            orphan_total += len(orphans)
            logger.error("表 %s 存在 users 中没有的孤儿键 %d 个（无法插入）: %s",
                         name, len(orphans), orphans[:5])
    if orphan_total:
        logger.error("检测到 %d 个孤儿键，请先修正 JSON 再迁移（本次中止，未写库）", orphan_total)
        sys.exit(1)

    # 2. 备份为 .json.bak（存在即保留首份；绝不删除原文件）
    for name, fname, _ in STORES:
        src = os.path.join(_BACKEND_DIR, fname)
        if not os.path.exists(src):
            continue
        bak = src + ".bak"
        if os.path.exists(bak):
            logger.info("备份已存在，保留首份: %s", bak)
        elif args.dry_run:
            logger.info("[dry-run] 将备份 %s → %s", src, bak)
        else:
            shutil.copy2(src, bak)
            logger.info("已备份 %s → %s", src, bak)

    if args.dry_run:
        # dry-run：报告将写入的行数（以库为参照的 diff 无法预知，按全量 upsert 上限口径报告）
        logger.info("[dry-run] 将建表（幂等）并逐表 diff 同步：")
        for name, _, attr in STORES:
            logger.info("[dry-run]   %-14s 源 %d 条 → 表 %s", name, len(sources[name]), attr)
        logger.info("[dry-run] 未写库、未建表。")
        return

    # 3. 建表（幂等，含 jti_blacklist / entity_embeddings）
    db.init_schema()
    logger.info("建表完成（IF NOT EXISTS 幂等）")

    # 4. 按外键顺序 diff 同步：以库现状 prime 快照 → sync_store 只写差异
    for name, _, attr in STORES:
        table = getattr(db, attr)
        current = db.load_table(table, "username")
        db.prime_snapshot(table, current)
        t0 = time.time()
        ops = db.sync_store(table, "username", sources[name])
        logger.info("同步 %-14s：差异 %d 项，耗时 %.2fs", name, ops, time.time() - t0)

    # 5. 计数校验
    failed = []
    for name, _, attr in STORES:
        table = getattr(db, attr)
        cnt = db.table_count(table)
        ok = cnt == len(sources[name])
        logger.info("计数校验 %-14s JSON=%d MySQL=%d %s",
                    name, len(sources[name]), cnt, "OK" if ok else "!! 不一致")
        if not ok:
            failed.append(name)

    # 6. 抽样逐字段比对
    rng = random.Random(20260902)  # 固定种子：重跑抽样一致，便于对账
    for name, _, attr in STORES:
        table = getattr(db, attr)
        loaded = db.load_table(table, "username")
        keys = list(sources[name])
        picks = rng.sample(keys, min(args.sample, len(keys)))
        mismatch = 0
        for k in picks:
            if canon(sources[name][k]) != canon(loaded.get(k)):
                mismatch += 1
                logger.error("抽样比对不一致 %s[%s]", name, k)
                logger.error("  JSON: %s", json.dumps(sources[name][k], ensure_ascii=False)[:300])
                logger.error("  MySQL: %s", json.dumps(loaded.get(k), ensure_ascii=False)[:300])
        logger.info("抽样比对 %-14s 抽取 %d 条，不一致 %d 条", name, len(picks), mismatch)
        if mismatch:
            failed.append(name)

    if failed:
        logger.error("迁移校验失败表: %s —— 请检查后重跑（脚本幂等）", sorted(set(failed)))
        sys.exit(1)
    logger.info("迁移完成：计数与抽样校验全部通过。可切换 STORE_BACKEND=sql 验证运行时")


if __name__ == "__main__":
    main()
