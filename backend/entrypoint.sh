#!/bin/sh
# 后端容器启动脚本：等 Neo4j 就绪 -> 空库时一次性导入图谱+迁移+向量索引（幂等）-> 起 uvicorn
set -e
cd /app

echo "[entrypoint] 等待 Neo4j 就绪 ..."
i=0
while [ $i -lt 60 ]; do
  if python -c "import os;from neo4j import GraphDatabase;d=GraphDatabase.driver(os.environ['NEO4J_URI'],auth=(os.environ['NEO4J_USER'],os.environ['NEO4J_PASSWORD']));d.verify_connectivity();d.close()" 2>/dev/null; then
    echo "[entrypoint] Neo4j 已就绪"; break
  fi
  i=$((i+1)); echo "[entrypoint]   ...waiting ($i/60)"; sleep 3
done

# 检测图库是否已有数据：为空才导入（数据经 neo4j-data 卷持久化，二次启动秒过）
COUNT=$(python -c "import os;from neo4j import GraphDatabase;d=GraphDatabase.driver(os.environ['NEO4J_URI'],auth=(os.environ['NEO4J_USER'],os.environ['NEO4J_PASSWORD']));s=d.session();print(s.run('MATCH (n) RETURN count(n) AS c').single()['c']);d.close()")

if [ "$COUNT" = "0" ]; then
  echo "[entrypoint] 图库为空，开始导入 medical.json（约 4.4 万实体 / 30 万关系，需数分钟）..."
  python /app/build_medicalgraph.py
  echo "[entrypoint] 执行阶段二幂等迁移（索引属性/别名/IDF 等）..."
  python /app/backend/scripts/migrate_graph_phase2.py
  echo "[entrypoint] 构建并填充阶段三向量索引 ..."
  python /app/backend/scripts/build_vector_index.py
  echo "[entrypoint] 导入完成"
else
  echo "[entrypoint] 检测到已有 $COUNT 个节点，跳过导入"
fi

echo "[entrypoint] 启动后端 (uvicorn app:app :8000) ..."
cd /app/backend
exec uvicorn app:app --host 0.0.0.0 --port 8000
