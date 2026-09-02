# ============================================================
# 医疗知识图谱系统 · 工程化入口（WSL2 统一环境）
#
# 一键启动：  make up        （基础设施容器 + 后端；前端用 make up-all）
# 常用：      make help / make status / make logs / make down
# 约定：      所有进程以后台方式运行，PID 记于 .run/，日志记于 .run/logs/
# ============================================================

SHELL      := /usr/bin/env bash
ROOT       := $(CURDIR)
BACKEND    := $(ROOT)/backend
FRONTEND   := $(ROOT)/frontend
PY         := $(BACKEND)/.venv/bin/python
RUN        := $(ROOT)/.run
LOGS       := $(RUN)/logs

BACKEND_PORT  ?= 8000
FRONTEND_PORT ?= 5173

.PHONY: help doctor deps infra up up-all backend frontend status logs logs-backend logs-frontend \
        stop stop-backend stop-frontend down restart-backend test test-json test-sql \
        migrate rebuild-graph build-vectors clean

help: ## 列出全部目标
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------- 环境自检（对应落地方案 W0） ----------

doctor: ## W0 环境自检：代码路径/Python/docker/依赖 五项检查
	@echo "[1/5] 工作目录: $(ROOT) （须以 /home/ 开头，非 /mnt/c/）"; \
	case "$(ROOT)" in /home/*) echo "    OK";; /mnt/c/*) echo "    !! 代码在 /mnt/c，reload/HMR 会失效";; *) echo "    ?";; esac; \
	echo "[2/5] Python:"; which python3; python3 --version 2>&1 | sed 's/^/    /'; \
	echo "[3/5] docker daemon:"; docker ps --format '    {{.Names}}: {{.Status}}' 2>&1 | head -5; \
	echo "[4/5] 后端 venv 依赖:"; $(PY) -c "import httpx, sqlalchemy, pymysql, neo4j; print('    OK httpx+sqlalchemy+pymysql+neo4j')" 2>&1; \
	echo "[5/5] 前端 node_modules:"; test -d $(FRONTEND)/node_modules && echo "    OK" || echo "    !! 缺失，跑 make deps"

deps: ## 安装后端 venv 与前端依赖（WSL 内）
	cd $(BACKEND) && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
	cd $(FRONTEND) && npm install

# ---------- 基础设施（Neo4j + MySQL 容器） ----------

infra: ## 启动 neo4j + mysql 容器并等待健康
	docker compose up -d neo4j mysql
	@echo "等待 neo4j 健康 ..."; \
	for i in $$(seq 1 60); do docker inspect --format '{{.State.Health.Status}}' medicalkg-neo4j-1 2>/dev/null | grep -q healthy && break; sleep 2; done; \
	echo "等待 mysql 健康 ..."; \
	for i in $$(seq 1 60); do docker inspect --format '{{.State.Health.Status}}' medicalkg-mysql-1 2>/dev/null | grep -q healthy && break; sleep 2; done; \
	docker ps --format '{{.Names}}: {{.Status}}' | sed 's/^/  /'

# ---------- 应用进程 ----------

up: infra backend ## ★ 一键启动：容器 + 后端（前端另跑 make frontend 或 make up-all）

up-all: infra backend frontend ## 一键启动：容器 + 后端 + 前端

backend: $(RUN)/logs ## 后台启动后端 uvicorn（已在跑则先停旧的）
	@. $(ROOT)/scripts/devtools.sh; kill_pidfile $(RUN)/backend.pid; kill_on_port $(BACKEND_PORT); \
	(cd $(BACKEND) && exec .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port $(BACKEND_PORT) \
	  > $(LOGS)/backend.log 2>&1) & echo $$! > $(RUN)/backend.pid; \
	echo "等待后端就绪 ..."; \
	for i in $$(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:$(BACKEND_PORT)/docs && { echo "  后端 OK -> http://localhost:$(BACKEND_PORT) (docs: /docs)"; exit 0; }; sleep 1; done; \
	echo "  !! 后端 60s 未就绪，查 $(LOGS)/backend.log"; tail -5 $(LOGS)/backend.log; exit 1

run: infra ## 前端开发模式之外，后端前台热重载（占住终端，Ctrl-C 停止）
	cd $(BACKEND) && .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port $(BACKEND_PORT) --reload

frontend: $(RUN)/logs ## 后台启动前端 vite dev
	@. $(ROOT)/scripts/devtools.sh; kill_pidfile $(RUN)/frontend.pid; kill_on_port $(FRONTEND_PORT); \
	(cd $(FRONTEND) && exec npm run dev -- --port $(FRONTEND_PORT) > $(LOGS)/frontend.log 2>&1) & echo $$! > $(RUN)/frontend.pid; \
	echo "等待前端就绪 ..."; \
	for i in $$(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:$(FRONTEND_PORT)/ && { echo "  前端 OK -> http://localhost:$(FRONTEND_PORT)"; exit 0; }; sleep 1; done; \
	echo "  !! 前端 60s 未就绪，查 $(LOGS)/frontend.log"; tail -5 $(LOGS)/frontend.log; exit 1

$(RUN)/logs:
	mkdir -p $(LOGS)

# ---------- 观测与停止 ----------

status: ## 容器/端口/最近日志一览
	@echo "== 容器 =="; docker ps --format '  {{.Names}}: {{.Status}}' | grep -E 'neo4j|mysql' || echo "  (无数据库容器在跑)"; \
	echo "== 应用端口 =="; \
	for p in $(BACKEND_PORT) $(FRONTEND_PORT); do \
	  if curl -s -o /dev/null -m 2 http://127.0.0.1:$$p/ ; then echo "  :$$p 在跑"; else echo "  :$$p 未启动"; fi; done; \
	echo "== 后端日志尾 =="; tail -3 $(LOGS)/backend.log 2>/dev/null | sed 's/^/  /' || true

logs: ## 跟随后端+前端日志（Ctrl-C 退出）
	tail -F $(LOGS)/backend.log $(LOGS)/frontend.log

logs-backend: ## 后端日志尾 50 行
	tail -50 $(LOGS)/backend.log

logs-frontend: ## 前端日志尾 50 行
	tail -50 $(LOGS)/frontend.log

stop-backend: ## 停止后端进程
	@. $(ROOT)/scripts/devtools.sh; kill_pidfile $(RUN)/backend.pid; kill_on_port $(BACKEND_PORT); echo "后端已停"

stop-frontend: ## 停止前端进程
	@. $(ROOT)/scripts/devtools.sh; kill_pidfile $(RUN)/frontend.pid; kill_on_port $(FRONTEND_PORT); echo "前端已停"

stop: stop-backend stop-frontend ## 停止前后端应用进程（容器不动）

down: stop ## 停止全部（含数据库容器，数据卷保留）
	docker compose stop neo4j mysql
	@echo "已全部停止（数据保留在 docker 卷中）"

restart-backend: backend ## 重启后端

# ---------- 数据与测试 ----------

migrate: ## 五处 JSON → MySQL 幂等迁移（含备份/计数/抽样校验）
	cd $(BACKEND) && .venv/bin/python scripts/migrate_json_to_mysql.py

rebuild-graph: ## 重建图谱（幂等 MERGE 导入 + 阶段二迁移）
	cd $(ROOT) && $(PY) build_medicalgraph.py
	cd $(BACKEND) && .venv/bin/python scripts/migrate_graph_phase2.py

build-vectors: ## 重建/增量填充向量索引（先跑 --dry-run 估费用）
	cd $(BACKEND) && .venv/bin/python scripts/build_vector_index.py --dry-run

test: test-json test-sql ## 契约测试：json 与 sql 双后端

test-json:
	cd $(BACKEND) && STORE_BACKEND=json .venv/bin/python -m pytest tests/ -q

test-sql:
	cd $(BACKEND) && STORE_BACKEND=sql MYSQL_DATABASE=medicalkg_test .venv/bin/python -m pytest tests/ -q

clean: ## 清理 .run/ 运行产物（不碰数据/日志外的东西）
	rm -rf $(RUN)
