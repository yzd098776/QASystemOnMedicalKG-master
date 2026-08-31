# 医疗知识图谱智能问答与可视化系统

> 基于 4.4 万医疗实体 + 30 万关系的知识图谱，融合 DeepSeek 大模型，提供智能问答、疾病自查、用药安全、健康计划等一站式医疗健康服务。

---

## 一、项目背景

本系统基于刘焕勇开源的 [QABasedOnMedicalKnowledgeGraph](https://github.com/liuhuanyong/QABasedOnMedicalKnowledgeGraph) 项目进行深度扩展。原项目采用命令行交互方式，仅支持基于规则引擎的简单问答。本项目在保留原有 Neo4j 知识图谱数据（7 类实体、11 类关系）的基础上，完成了以下核心升级：

- **全新 Vue 3 前端**：完全替换原有命令行界面，构建现代化 Web 应用
- **FastAPI 后端**：从零搭建 RESTful API 服务，替代原有脚本式调用
- **大模型集成**：接入 DeepSeek API，实现流式输出的智能问答
- **6 大特色功能**：疾病自查、用药安全、健康计划、就医指南、知识百科、健康档案
- **全面性能优化**：ECharts 按需引入、后端 N+1 查询修复、异步化、缓存层等
- **数据质量与图谱能力增强（阶段二）**：索引/唯一约束自动建立、口语别名归一化、图谱幂等迁移（外部编码占位、关系属性补全、疾病先验、症状 IDF）、先验加权诊断

---

## 二、技术架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户浏览器 (http://localhost:5173)      │
├─────────────────────────────────────────────────────────┤
│  Vue 3 + Vite + Element Plus + ECharts + Tailwind CSS   │
│       「空间沉浸」设计：毛玻璃卡片 + 3D 透视 + 渐变背景     │
│  ┌─────────┬─────────┬─────────┬─────────┬───────────┐  │
│  │ 知识图谱 │ 智能问答 │ 疾病自查 │ 用药安全 │ 健康管理  │  │
│  └─────────┴─────────┴─────────┴─────────┴───────────┘  │
├─────────────────────────────────────────────────────────┤
│           Vite Dev Proxy → http://localhost:8000         │
├─────────────────────────────────────────────────────────┤
│                   FastAPI 后端 (Python)                   │
│  ┌─────────┬─────────┬─────────┬─────────┬───────────┐  │
│  │ 用户认证 │ 图谱查询 │ 诊断推理 │ AI问答   │ 健康管理  │  │
│  └─────────┴─────────┴─────────┴─────────┴───────────┘  │
├──────────────┬──────────────────────┬───────────────────┤
│   Neo4j 5.26  │   DeepSeek API      │   JSON 文件存储    │
│   (知识图谱)   │   (大模型问答)       │   (用户/档案)     │
└──────────────┴──────────────────────┴───────────────────┘
```

### 2.2 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| **前端框架** | Vue 3 + Vite 8.x | Composition API + `<script setup>` |
| **UI 组件库** | Element Plus 2.14.x | 自动导入，中文适配 |
| **图谱可视化** | ECharts 6.x | 力导向布局关系图（按需引入，仅加载 GraphChart） |
| **状态管理** | Pinia 3.x | 用户认证与健康档案（shallowRef 优化） |
| **路由** | Vue Router 4.x | 路由守卫 + JWT 鉴权 + 404 兜底 |
| **HTTP 客户端** | Axios / Fetch | Axios 用于普通请求，Fetch 用于 SSE 流式 |
| **样式方案** | Tailwind CSS 3.x | 原子化 CSS + 自定义设计系统（「空间沉浸」风格） |
| **字体** | Plus Jakarta Sans / Inter / JetBrains Mono | 标题 / 正文 / 数据 |
| **Markdown 渲染** | marked + DOMPurify | 安全的 Markdown 解析与 XSS 防护 |
| **后端框架** | FastAPI | 异步高性能，自动生成 API 文档 |
| **图数据库** | Neo4j 5.26 社区版 | 存储 4.4 万实体 + 30 万关系 |
| **AI 模型** | DeepSeek Chat | 流式输出，支持上下文对话 |
| **认证方案** | JWT + bcrypt | 30 分钟 access token + 7 天 refresh token，登出即失效；密码哈希加固（超长密码先 sha256 再 bcrypt） |
| **环境配置** | python-dotenv | `.env` 文件管理敏感配置 |

### 2.3 数据模型

**7 类实体节点：**

| 实体类型 | 英文标签 | 颜色编码 | 示例 |
|---------|---------|---------|------|
| 疾病 | Disease | 🔴 红色 | 感冒、高血压、糖尿病 |
| 药品 | Drug | 🔵 蓝色 | 阿莫西林、板蓝根 |
| 症状 | Symptom | 🟢 绿色 | 发烧、咳嗽、头痛 |
| 食物 | Food | 🟡 黄色 | 姜汤、鸡蛋 |
| 检查 | Check | 🟣 紫色 | 血常规、CT |
| 科室 | Department | 🟠 橙色 | 呼吸内科、心内科 |
| 在售药品 | Producer | ⚪ 灰色 | 各药品生产商 |

**11 类关系边：**

| 关系类型 | 说明 | 示例 |
|---------|------|------|
| has_symptom | 疾病→症状 | 感冒→发烧 |
| acompany_with | 并发症 | 感冒→肺炎 |
| common_drug | 常用药 | 感冒→阿莫西林 |
| recommand_drug | 推荐药 | 感冒→板蓝根 |
| do_eat | 宜吃食物 | 感冒→姜汤 |
| no_eat | 忌吃食物 | 感冒→油条 |
| recommand_eat | 推荐食物 | 感冒→薏米莲子粥 |
| need_check | 需要检查 | 感冒→血常规 |
| belongs_to | 所属科室 | 感冒→呼吸内科 |
| drugs_of | 在售厂商 | 药品→生产商 |
| - | 症状→疾病 | 反向查询 |

**阶段二新增属性与索引（由幂等迁移/启动自检维护，只增不删）：**

| 项目 | 说明 |
|------|------|
| `Disease.icd10` | ICD-10 编码，当前为 `""` 占位（无权威来源，待对齐后回填，不编造） |
| `Drug.atc` | ATC 编码，当前为 `""` 占位（同上） |
| `Disease.get_prob` | 疾病患病先验（百分比数值），源自 `data/medical.json` 的 `get_prob` 字段 |
| `Symptom.idf` | 症状逆文档频率 `log(1+总疾病数/关联疾病数)`，越常见的症状权重越低 |
| 关系属性 | `has_symptom / common_drug / do_eat / no_eat / need_check / acompany_with / belongs_to / drugs_of` 八类关系补齐 `weight`（默认 1.0）、`source`（默认 `"medical.json"`）、`evidence_level`（默认 `"unverified"`，默认值含义见迁移脚本注释） |
| 索引/约束 | 七类实体标签的 `name` 唯一约束（存在重名的标签自动降级普通索引并告警，重名清洗后重跑可自动升级回约束）；无标签实体定位查询（实体详情/路径/关联）已改写为逐标签分支命中标签级索引，dbHits 从 ~4.4 万降至两位数；后端启动与建图脚本均幂等执行 |
| 别名归一化 | `backend/data/aliases.json` 收录口语别名→规范实体名映射，覆盖疾病/症状/药品三类（如 感冒→上呼吸道感染、扑热息痛→对乙酰氨基酚片），具体条数以词典文件为准；图谱查询类接口统一先归一化再查询，未命中原词兜底再查 |

---

## 三、核心功能

### 3.1 用户认证与健康档案

- **注册/登录**：用户名唯一校验、邮箱格式验证、密码强度校验（8 位以上含字母数字）、密码哈希加固存储（超长密码先 sha256 摘要再 bcrypt，旧哈希登录时静默升级）
- **JWT 认证**：30 分钟 access token + 7 天 refresh token，前端 401 时自动单飞刷新并重放请求；登出即时失效（jti 黑名单 + token_version 版本校验），路由守卫自动拦截未登录请求；登录/注册与问答接口均带滑动窗口限流（429 + Retry-After）
- **数据主权**：支持一键导出个人全部数据（`/api/user/export`）与彻底删除账号及数据（`/api/user/data`）；健康档案敏感字段（过敏史/病史/家族史）落盘前 Fernet 加密，读出时解密
- **健康档案**：支持维护年龄、性别、身高、体重、血型、过敏史（药品/食物）、既往病史、家族病史、生活习惯
- **个性化联动**：问答和推荐功能自动带入匿名化档案信息

### 3.2 知识图谱可视化大屏

- **图谱展示**：ECharts 力导向布局，支持缩放、拖拽、节点点击、关系悬停
- **颜色编码**：7 类实体分别用不同颜色标识，顶部图例可筛选
- **搜索定位**：模糊搜索实体，点击后以该实体为中心重新加载关联图谱
- **路径发现**：双实体选择器，查找最短路径并高亮展示，文字化关联说明
- **实体详情**：右侧抽屉式卡片，根据实体类型动态展示百科信息
- **咨询 AI**：每个实体均可一键跳转至问答页面，自动填入相关提问
- **工具栏**：放大/缩小/重置、导出 PNG 图片、按类型筛选
- **外部跳转**：从百科页面点击"在知识图谱中查看"可定位到具体实体

### 3.3 智能问答系统（GraphRAG）

- **聊天界面**：类 ChatGPT 布局，左侧历史会话列表，右侧聊天窗口；急症命中时渲染红色警示卡
- **流式输出**：基于 Fetch + ReadableStream 实现打字机效果，支持中途停止；SSE 帧协议详见 3.9 节
- **对话持久化**：聊天记录自动同步后端，支持重命名、删除、一键清空
- **DeepSeek 集成**：后端代理转发，API 密钥安全存储在服务端 `.env`
- **GraphRAG 管线**：意图路由（规则+词典，零 LLM）→ 图谱三元组检索增强 → 句子级来源溯源（详见 3.9 节）
- **降级方案**：未配置 API 密钥时，自动从知识图谱查询并格式化回答，不报错
- **系统提示词**：约束 AI 仅依据图谱三元组回答，禁止给出用药剂量与手术方案
- **答案溯源**：句子级溯源，【T#】标记渲染为可点击角标，来源卡片展示三元组并可跳转知识图谱
- **XSS 防护**：使用 marked + DOMPurify 安全渲染 Markdown

### 3.4 多症状疾病自查

- **症状选择器**：按 8 大系统分类（呼吸/消化/神经/心血管/运动/皮肤/泌尿/全身）
- **多选支持**：支持添加症状持续时间和严重程度，输入症状先经别名归一化（如「发烧」→规范症状）
- **智能推理**：匹配分 = Σ命中症状的 IDF 权重（越常见的症状权重越低）+ 疾病先验阻尼项 `log(1+get_prob)`，按分数降序排序；结果新增 `match_evidence`（每条命中症状的权重与贡献分）与 `prior` 字段，`probability` 归一化为 0-100 相对置信度（既有响应字段均保留）
- **结果展示**：疾病名称、匹配症状、简介、建议检查、推荐科室
- **一键咨询**：自查结果自动带入问答系统

### 3.5 用药安全与禁忌查询

- **药品禁忌**：输入药品名称，显示禁忌人群、忌吃食物、关联疾病
- **食物-疾病禁忌**：双向查询，食物→不宜食用的疾病，疾病→宜吃/忌吃食物
- **药物相互作用**：多选药品查询相互作用，显示风险等级
- **个人提醒**：结合健康档案自动高亮用户需要注意的禁忌

### 3.6 个性化健康管理

- **疾病预防计划**：基于年龄、性别、家族病史推荐高发疾病预防措施
- **慢性病管理**：支持输入任意疾病名称生成专属管理计划（饮食/运动/检查/用药提醒）
- **健康日历**：记录体重、血压、血糖、心率等健康数据，支持单条删除
- **计划历史**：AI 生成的计划自动持久化存储，支持历史回看与一键清理

### 3.7 就医指南与科室导航

- **科室推荐**：输入症状或疾病，自动推荐就诊科室
- **检查项目说明**：检查目的、流程、注意事项、正常值范围
- **就医流程指引**：挂号→就诊→检查→取药→住院全流程说明
- **常见问题**：医保报销、预约挂号等 FAQ

### 3.8 医疗知识百科

- **分类浏览**：按疾病/药品/症状/检查/科室分类展示
- **搜索过滤**：支持模糊搜索，分页加载
- **每日科普**：每日随机推荐健康科普文章
- **实体详情**：百科卡片展示完整属性和关联实体
- **咨询 AI**：每个实体均可一键跳转问答，自动填入相关提问

### 3.9 GraphRAG 问答管线架构（阶段三）

`/api/chat` 的全部问答逻辑收敛于 `backend/services/` 服务包，路由函数瘦身为参数校验 + 管线调用。对最后一条用户消息的处理分支如下（优先级自上而下）：

```
用户消息
  │
  ├─ 1. 急症红牌检测（services/emergency.py，最高优先级）
  │     命中 red_flags.json 关键词/模式（且非「什么是X」类定义提问）→ emergency 帧 + 固定急诊引导 → [DONE]
  │     不进行图谱检索、不调用 LLM
  │
  ├─ 2. 实体识别（services/entity_recognizer.py）
  │     dict/*.txt 词典正向最大匹配 + aliases.json 别名归一化 + 否认词过滤，零 LLM 调用
  │
  └─ 3. 意图路由（services/intent_router.py，规则优先）
        ├─ drug_safety   ≥2 种药品实体 → 复用药物相互作用逻辑，不调 LLM
        ├─ diagnosis     ≥2 症状实体或自查措辞 → 复用 /api/diagnosis 加权诊断，不调 LLM
        ├─ graph_lookup  单实体 + 百科措辞 → 图谱数据组织结构化回答，不调 LLM
        └─ rag           其余 → 混合检索三元组 → DeepSeek 流式回答（无密钥时降级为图谱结构化回答）
```

**SSE 帧协议**（既有帧不变，仅新增类型）：

| 帧 | 时机 | 说明 |
| --- | --- | --- |
| `data: {"content": "..."}` | 流式过程中 | 流式文本（既有，【T#】来源标记保留原文输出） |
| `data: [DONE]` | 结束 | 既有结束帧 |
| `data: {"emergency": true, "message": ..., "guidance": ...}` | 最先 | 急症红牌，命中即终止后续检索 |
| `data: {"intent": "..."}` | 可选首帧 | 说明命中的意图分支 |
| `data: {"sources": [{id, subject, predicate, object, sentences}], "has_uncited": bool}` | 内容流结束后 | 被引用的图谱三元组及引用句序号 |

**检索增强（rag 分支）**：实体识别结果 → 混合检索锤定实体（关键词路：别名归一 + `CONTAINS`；向量路：余弦相似）双路加权融合（权重见 `.env` 的 `HYBRID_KEYWORD_WEIGHT` / `HYBRID_VECTOR_WEIGHT`）→ 每实体取 1-2 跳邻居三元组（单实体≤20 条、总量≤60 条）→ 分配稳定编号 T1..Tn 注入提示词 → 模型仅依据三元组回答并在引用句末标注【T1】【T3】→ 后端解析标记生成 sources 帧。

**向量检索方案与局限**：选型为 Neo4j 5.26 原生 vector index（免独立向量库运维），嵌入采用**纯 Python 哈希字符 n-gram 嵌入**（blake2b 特征哈希，默认 256 维，零新增依赖）。局限：哈希嵌入只能捕捉字面重合，**不具备语义理解能力**，对「脑子里嗡嗡响」这类口语召回依赖字面字符重合，效果有限。升级路径：未来可换用真实嵌入模型（如 sentence-transformers 或远程嵌入 API），仅需替换 `services/vector_index.py` 的 `embed()` 函数并重跑 `backend/scripts/build_vector_index.py` 重建索引（注意维度一致）。填充命令：`cd backend; python scripts/build_vector_index.py`（幂等，支持 `--limit` 试跑）。

**急症红牌（3.6）**：关键词/模式表存于 `backend/data/red_flags.json`（中文注释、可扩展）。命中后先发 `emergency` 帧，再发固定急诊引导（立即拨打 120/前往急诊 + 免责声明），直接 `[DONE]`，**不进行图谱检索与 LLM 调用**，前端渲染为红色警示卡。为降低误报，「什么是胸痛」「休克的含义」这类**概念性定义提问**（命中强定义句式、且不含第一人称/急迫措辞）会豁免红牌、交回意图路由按百科/检索作答；任何真急症自述（如「我突然胸痛喘不上气」）仍立即触发，判据保守以安全优先。

**Text2Cypher 长尾覆盖（白名单）**：仅在配置了 `DEEPSEEK_API_KEY` 且常规检索三元组不足（<3 条）时触发一次。后端不信任模型输出，强制校验：去注释后仅允许只读子句（MATCH / OPTIONAL MATCH / WHERE / WITH / RETURN / ORDER BY / SKIP / LIMIT / DISTINCT / AS / UNION），出现 CREATE/MERGE/DELETE/DETACH/SET/REMOVE/DROP/CALL/FOREACH/分号/多语句一律拒绝；无 LIMIT 自动追加 `LIMIT 50`；执行用带超时的 **Neo4j 只读访问模式**事务（`TEXT2CYPHER_TIMEOUT`；即便白名单校验漏网，服务端仍会拒绝任何写子句，形成纵深防御），结果行数上限 50。校验/执行/超时任一失败自动降级回常规检索增强流程。可通过 `TEXT2CYPHER_ENABLED=false` 关闭。

### 3.10 性能与渲染优化（阶段四）

**缓存层（`backend/core/cache.py`）**：统一 `CacheBackend` 抽象，默认后端为进程内 **LRU**（容量上限 `CACHE_MAX_ENTRIES`=1024 + 每键 TTL），适配**默认单 worker 部署**（uvicorn 单进程）。配置 `.env` 的 `REDIS_URL` 后自动切换为 **Redis** 后端实现多 worker/多实例共享（`redis` 为可选依赖 `pip install "redis[hiredis]"`，未安装或连接失败告警并降级本地 LRU，零强制新增依赖）。当前缓存点：
- 实体详情 `/api/kg/entity/{name}`（`ENTITY_CACHE_TTL`，命中跳过 5 路 `OPTIONAL MATCH` 聚合）；
- 全库实体总数 `kg_total_count`；
- 用户数据导出 `/api/user/export`（含解密的五源聚合，短 TTL 快照）——**档案更新 / 删号即时失效**（`invalidate_user_caches`），其余数据写入以 TTL 最终一致；
- 提供 `invalidate_entity(name)` 按实体精准失效（图谱经脚本更新后可调用）。

> ⚠️ 多 worker 部署须知：本地 LRU 为进程内实现，多 worker 各进程缓存相互独立、不保证跨进程一致；需要多 worker 或水平扩容时**必须配置 `REDIS_URL`**（同时可消解阶段一遗留的「限流/令牌黑名单进程内不共享」问题）。

**游标翻页（`/api/kg/entities`）**：保留既有 `page`/`limit` 契约，所有分支统一补 `ORDER BY n.name`（修正原先无排序时 `SKIP` 深翻页可能重复/遗漏），并新增可选 `cursor` 参数——以 `WHERE n.name > $cursor` 定位代替大 `OFFSET`，深翻页耗时平稳；响应追加 `next_cursor`（本页满 `limit` 时给出末实体名，无更多为 `null`；别名两路合并模式不做游标深翻、返回 `null`，页码仍可用）。游标依赖阶段二建立的有序索引（`Disease` 为普通索引亦支持 `ORDER BY name`）。

**慢查询日志**：`run_cypher` / `run_readonly` 统一计时埋点（`services/graph_db.py`），执行耗时超过 `SLOW_QUERY_MS`（默认 200ms）记 WARNING，用于定位性能热点。

**图谱渲染（`/api/kg/neighbors`）**：新增增量接口 `GET /api/kg/neighbors?name=&depth=1|2&limit=`（**不改动 `/api/kg/related` 契约**），逐跳各带独立 `LIMIT` 逐层扩展、避免变长路径一次性展开爆炸。前端 `KnowledgeGraphView` 维护累积的 `allNodes/allLinks`，「展开下一跳」合并进当前子图而非整体重绘；并实现 **LOD**：节点数 >150 时隐藏文字标签（保留悬停 tooltip）以保帧率。首屏仍以选定实体 `depth=1` 局部展开，非一次性渲染全库。

**前端体积（`vite.config.js` manualChunks）**：将第三方库拆分为独立可并行加载/长期缓存的 chunk。拆分前后主要产物（minify 后）：

| chunk | 拆分前 | 拆分后 |
| --- | --- | --- |
| 主 `index` | ~989 KB（含全部 vendor） | **5.65 KB** |
| `KnowledgeGraphView` | ~493 KB（含 echarts） | **16 KB** |
| `ChatView` | ~81 KB | **15 KB** |
| `vendor-echarts` | （混入主/视图 chunk） | 477 KB（独立） |
| `vendor-element-plus` | （混入主 chunk） | 1015 KB（独立，长期缓存） |
| `vendor-vue` / `vendor-utils` | （混入主 chunk） | 31 KB / 111 KB |

警告线自 1000 恢复为 **500 KB**（真实阈值）：业务路由 chunk 均已 <20 KB，构建警告仅剩 `vendor-element-plus`/`vendor-echarts` 两个**第三方库体积下限**（独立 chunk、命中长期缓存，非业务代码问题）。

**渲染引擎对比结论（默认不迁移）**：项目当前用 ECharts GraphChart（按需引入）+ 力导向。
- **Cytoscape.js**：交互与样式表达强、`fcose`/`cola` 等布局对数十万级静态图分析更优；但体积约 ~1MB、需重写现有渲染层。
- **sigma.js**（配 graphology）：WebGL 渲染，>1 万节点仍流畅；样式定制门槛高、生态迁移成本大。
- **结论**：本系统的诉求是「以选中实体为中心的局部子图 + 增量展开 + LOD」，ECharts 按需引入已满足且与现有代码深度耦合，迁移收益不匹配成本；**默认维持 ECharts**，仅当将来需要全库级交互式图分析时再评估 sigma（WebGL）。


---

## 四、项目结构

```
QASystemOnMedicalKG-master/
├── README.md                           # 项目文档
├── .gitignore                          # Git 忽略规则（含 .env）
├── build_medicalgraph.py               # Neo4j 数据导入脚本
│
├── backend/                            # FastAPI 后端
│   ├── app.py                          # 全部 API 接口（认证/图谱/问答/自查/用药/健康）
│   ├── .env                            # 环境变量（DEEPSEEK_API_KEY 等，不入库）
│   ├── requirements.txt                # Python 依赖
│   ├── core/                           # 核心模块
│   │   ├── config.py                   # 配置加载与强校验（.env）
│   │   ├── security.py / crypto.py     # 认证与档案字段加密（阶段一）
│   │   ├── ratelimit.py                # 滑动窗口限流（阶段一）
│   │   ├── graph_index.py              # 七类标签索引/唯一约束幂等检查（阶段二）
│   │   └── alias.py                    # 口语别名归一化（阶段二）
│   ├── data/
│   │   └── aliases.json                # 别名→规范实体名词典（阶段二）
│   ├── scripts/
│   │   └── migrate_graph_phase2.py     # 图谱幂等迁移脚本（阶段二，可独立重跑）
│   ├── users.json                      # 用户数据持久化
│   ├── profiles.json                   # 健康档案持久化
│   ├── health_records.json             # 健康日历记录持久化
│   ├── health_plans.json               # 健康计划历史持久化
│   └── chat_history.json               # 对话记录持久化
│
├── frontend/                           # Vue 3 前端
│   ├── index.html                      # 入口 HTML（字体异步加载）
│   ├── vite.config.js                  # Vite 配置（Element Plus 自动导入、API 代理）
│   ├── tailwind.config.js              # Tailwind CSS 配置（自定义色系/字体/阴影/动画）
│   ├── postcss.config.js               # PostCSS 配置
│   ├── package.json                    # 依赖声明
│   └── src/
│       ├── main.js                     # 应用入口（Element Plus、Pinia、Router、图标按需注册）
│       ├── style.css                   # 全局样式（设计令牌 + glass-card + Element Plus 覆盖）
│       ├── App.vue                     # 根组件
│       ├── router/index.js             # 路由配置（含 JWT 守卫 + 404 兜底）
│       ├── stores/index.js             # Pinia 状态管理（shallowRef 优化）
│       ├── utils/request.js            # Axios 实例（JWT 拦截、统一错误处理）
│       ├── components/
│       │   └── layout/MainLayout.vue   # 主布局（顶部浮动胶囊导航 + 3D 透视内容区）
│       └── views/
│           ├── auth/
│           │   ├── LoginView.vue       # 登录页（3D 浮动卡片 + 渐变光晕）
│           │   ├── RegisterView.vue    # 注册页
│           │   └── ProfileView.vue     # 健康档案管理
│           ├── kg/
│           │   └── KnowledgeGraphView.vue  # 知识图谱可视化（ECharts 按需引入）
│           ├── chat/
│           │   └── ChatView.vue        # 智能问答（SSE 流式 + XSS 防护）
│           ├── diagnosis/
│           │   └── DiagnosisView.vue   # 多症状疾病自查
│           ├── drug/
│           │   └── DrugSafetyView.vue  # 用药安全与禁忌查询
│           ├── health/
│           │   └── HealthPlanView.vue  # 个性化健康管理计划
│           ├── guide/
│           │   └── MedicalGuideView.vue # 就医指南与科室导航
│           └── wiki/
│               └── WikiView.vue        # 医疗知识百科
│
├── data/
│   └── medical.json                    # 医疗知识图谱源数据（47MB，4.4万实体+30万关系）
│
└── dict/                               # 特征词典
    ├── disease.txt                     # 疾病名称词典
    ├── symptom.txt                     # 症状词典
    ├── drug.txt                        # 药品词典
    ├── food.txt                        # 食物词典
    ├── check.txt                       # 检查项目词典
    ├── department.txt                  # 科室词典
    ├── producer.txt                    # 药品生产商词典
    └── deny.txt                        # 否定词词典
```

---

## 五、前端设计系统

前端采用「空间沉浸」设计风格 — 3D 景深 + 浮动层 + 毛玻璃态导航，灵感来源 Apple Vision Pro 空间界面。

### 5.1 配色方案

| 用途 | 色值 | Tailwind 类名 |
|------|------|--------------|
| 主色（靛蓝） | `#4F46E5` | `primary-500` |
| 强调色（蓝紫） | `#7C3AED` | `accent-500` |
| 成功/健康绿 | `#10B981` | `success-500` |
| 危险/警告红 | `#F43F5E` | `danger-500` |
| 背景渐变 | `#F0F4FF → #E8ECF4` | linear-gradient 135deg |
| 卡片背景 | `rgba(255,255,255,0.72)` | glass-card backdrop-blur |
| 正文色 | `#0F172A` | `surface-900` |

### 5.2 字体方案

| 用途 | 字体 |
|------|------|
| 标题 | Plus Jakarta Sans（font-display） |
| 正文 | Inter / Noto Sans SC |
| 数据/代码 | JetBrains Mono |

### 5.3 关键设计规范

- **布局**：顶部浮动胶囊导航栏（`border-radius: 999px`，毛玻璃 `backdrop-blur-xl`，`sticky top-3` 居中）+ 3D 透视内容区 `perspective(1200px)`
- **卡片**：`.glass-card` 半透明毛玻璃（`bg-white/72 + blur 20px`），圆角 16px，多层阴影模拟景深
- **3D 卡片**：`.float-card` 带 `rotateX(2deg)` 透视，hover 恢复水平 + 上浮 + 阴影加深
- **背景**：渐变底色 + SVG 网格纹理（CSS 20s 缓慢漂移）+ 环境光晕装饰
- **导航**：当前路由项主色渐变背景 + 白色文字 + 微发光阴影
- **表格**：表头半透明靛蓝背景，行 hover 微妙高亮
- **输入框**：毛玻璃背景 + focus 靛蓝外发光
- **页面切换**：3D 旋转过渡（`rotateY(6deg) + scale(0.97)` 入场，350ms）
- **按钮**：主色渐变 + 发光阴影 + 弹性缓动 `cubic-bezier(0.34,1.56,0.64,1)`

---

## 六、性能优化

### 6.1 前端优化

| 优化项 | 说明 | 效果 |
|-------|------|------|
| ECharts 按需引入 | 仅加载 `GraphChart + TooltipComponent + LegendComponent + CanvasRenderer` | 打包体积从 1.1MB 降至 ~480KB |
| Element Plus 图标按需注册 | 从 200+ 全量注册改为 ~31 个实际使用图标 | 减少 ~150KB |
| 路由懒加载 | 各页面组件动态 import | 首屏仅加载当前页面 |
| shallowRef | 大型只读数据（搜索结果、图谱数据）使用 shallowRef | 减少深层响应式开销 |
| CSS 精简 | 移除非核心元素的 `backdrop-filter`（输入框、卡片等） | 降低 GPU 合成开销 |
| SSE 行缓冲 | 处理跨 chunk 的不完整 data 行 | 防止流式输出丢失数据 |
| scroll 节流 | `requestAnimationFrame` 节流滚动 | 避免每 chunk 触发滚动 |
| 字体异步加载 | Google Fonts 改为 `preload + onload` 非阻塞加载 | 首屏不阻塞渲染 |
| XSS 防护 | `marked` + `DOMPurify` 替代正则渲染 | 消除 XSS 漏洞 |

### 6.2 后端优化

| 优化项 | 说明 | 效果 |
|-------|------|------|
| N+1 查询修复 | `get_entity_detail` 5次→1次、`diagnosis` 21次→1次 | 大幅减少 Neo4j 查询次数 |
| 异步化 | `run_cypher` 使用 `asyncio.to_thread` 包装 | 避免阻塞 FastAPI 事件循环 |
| 内存缓存 | 实体总数 5 分钟 TTL 缓存 | 减少重复数据库查询 |
| Neo4j 连接池 | `max_connection_pool_size=50` | 提升并发能力 |
| 安全加固 | 移除硬编码密钥，改用 `.env` + `python-dotenv` | 防止密钥泄露 |
| 错误处理 | `bare except` → `except Exception` + logging | 便于问题排查 |
| 时间修复 | `datetime.utcnow()` → `datetime.now(timezone.utc)` | 消除时区警告 |
| 索引与约束 | 七类实体 `name` 唯一约束（重名自动降级普通索引），启动/建图时幂等执行 | 查询提速且防重复实体 |
| 别名归一化 | 口语别名统一映射到规范实体名后查询，未命中原词兜底 | 提升召回率，避免 0 召回 |
| 诊断加权 | 症状 IDF + 疾病先验阻尼加权替代简单重合度计数 | 常见病与心血管等关键疾病排序更合理 |

---

## 七、部署指南

### 7.1 环境要求

| 依赖 | 版本要求 |
|------|---------|
| Node.js | >= 18.x |
| Python | >= 3.10 |
| Neo4j | >= 5.x（社区版即可） |
| npm | >= 9.x |

### 7.2 Neo4j 数据库

1. 安装并启动 Neo4j 5.x，访问 `http://localhost:7474`
2. 默认连接地址：`bolt://localhost:7687`，用户名 `neo4j`；密码不再硬编码，统一通过环境变量/`.env` 提供（建图脚本读取 `backend/.env` 的 `NEO4J_PASSWORD`，未配置时报错退出）
3. 导入数据（建图前自动幂等创建索引/约束）：

```bash
python build_medicalgraph.py

# 仅创建/校验索引与约束，不导入数据（幂等，可重复执行）
python build_medicalgraph.py --index-only
```

4. 存量图谱迁移（幂等、只增不删，可安全重跑；补齐外部编码占位、关系属性、疾病先验与症状 IDF）：

```bash
cd backend
python scripts/migrate_graph_phase2.py
```

脚本会打印每一步受影响行数与最终汇总；重复执行时占位与关系属性步骤应显示 0 行。
注意：`Disease.icd10` / `Drug.atc` 占位值为空字符串（非 NULL），未来做编码缺失检测时请用 `IS NULL OR = ''` 判断。

### 7.3 后端配置与启动

```bash
cd backend

# 安装 Python 依赖
pip install -r requirements.txt

# 配置环境变量（创建 .env 文件，可参考 .env.example）
# 以下为启动必填项，缺失或为弱值时后端拒绝启动：
# DEEPSEEK_API_KEY=sk-your-api-key-here（未配置时 AI 问答降级）
# NEO4J_PASSWORD=your-neo4j-password
# JWT_SECRET=（>=32 位强随机值，生成：python -c "import secrets; print(secrets.token_urlsafe(32))"）
# PROFILE_ENCRYPTION_KEY=（档案敏感字段加密密钥，生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"）

# 启动后端服务
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

后端 API 文档：http://localhost:8000/docs

### 7.4 前端启动

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 生产构建
npm run build
```

前端访问地址：http://localhost:5173

### 7.5 测试账号

| 用户名 | 密码 |
|-------|------|
| testuser | test12345 |

---

## 八、API 接口一览

| 模块 | 接口 | 方法 | 说明 |
|------|------|------|------|
| **认证** | `/api/auth/register` | POST | 用户注册（限流） |
| | `/api/auth/login` | POST | 用户登录，返回 access/refresh 双令牌（限流） |
| | `/api/auth/refresh` | POST | 刷新令牌轮换签发新令牌对 |
| | `/api/auth/logout` | POST | 登出，令牌即时失效 |
| **账户** | `/api/user/export` | GET | 导出当前用户全部数据 |
| | `/api/user/data` | DELETE | 删除账号及全部数据 |
| **档案** | `/api/profile/get` | GET | 获取健康档案 |
| | `/api/profile/update` | POST | 更新健康档案 |
| **图谱** | `/api/kg/entities` | GET | 搜索实体（支持类型/关键词/分页） |
| | `/api/kg/entity/{name}` | GET | 实体详情（含关联症状/药品/食物/检查） |
| | `/api/kg/path` | GET | 最短路径查询 |
| | `/api/kg/related` | GET | 关联实体查询（支持深度） |
| **自查** | `/api/diagnosis` | POST | 多症状疾病匹配（症状 IDF + 先验加权，结果含 `match_evidence`/`prior`） |
| **用药** | `/api/drug/contraindication` | GET | 药品禁忌查询 |
| | `/api/food/contraindication` | GET | 食物-疾病禁忌查询 |
| | `/api/drug/interaction` | POST | 药物相互作用查询 |
| **问答** | `/api/chat` | POST | 流式智能问答（SSE） |
| | `/api/chat/history` | GET/DELETE | 对话历史管理 |
| | `/api/chat/history/save` | POST | 保存对话记录 |
| **指南** | `/api/guide/department` | GET | 科室推荐 |
| | `/api/guide/check` | GET | 检查项目说明 |
| **健康** | `/api/health/prevention` | POST | 疾病预防计划（AI 生成） |
| | `/api/health/chronic` | POST | 慢性病管理计划（AI 生成） |
| | `/api/health/records` | GET/POST | 健康日历记录管理 |
| | `/api/health/records/{id}` | DELETE | 删除单条健康记录 |
| | `/api/health/plans` | GET/POST | 健康计划历史管理 |
| | `/api/health/plans` | DELETE | 一键清空所有计划 |
| **百科** | `/api/wiki/daily-tip` | GET | 每日健康科普 |

---

## 九、项目亮点

### 9.1 技术亮点

- **真正的流式输出**：基于 Fetch API + ReadableStream 实现 SSE 流式传输，后端使用 `httpx.aiter_lines()` 逐行推送，前端逐字渲染打字机效果
- **知识图谱可视化**：ECharts 力导向布局**按需渲染局部子图**（首屏以选定实体为中心 depth=1 展开，非一次性渲染全库 4.4 万实体）；「展开下一跳」通过 `/api/kg/neighbors` 增量合并节点/边，节点数超过 150 时启用 LOD 自动隐藏文字标签以保帧率；支持缩放/拖拽/筛选/导出、悬停高亮关联节点
- **ECharts 按需引入**：仅加载 GraphChart 所需模块，打包体积减少 60%
- **后端 N+1 优化**：将 21 次独立 Cypher 查询合并为 1 次，使用 OPTIONAL MATCH 一次返回所有关联数据
- **异步非阻塞**：Neo4j 同步驱动通过 `asyncio.to_thread` 包装，不阻塞 FastAPI 事件循环
- **降级容错**：未配置 DeepSeek API 时自动降级为基于知识图谱的结构化回答
- **「空间沉浸」设计系统**：Tailwind 自定义主题（靛蓝+蓝紫渐变色系）、毛玻璃 glass-card 组件、3D 透视浮动卡片、弹性缓动微交互、SVG 网格纹理动态背景、Plus Jakarta Sans 标题字体

### 9.2 产品亮点

- **个性化医疗**：健康档案自动带入问答上下文，实现"千人千面"的健康建议
- **多症状联合诊断**：基于图谱关系的概率推理，支持症状持续时间和严重程度
- **用药安全防护**：自动关联用户过敏史，高亮个人禁忌提醒
- **全链路打通**：自查→问答→图谱→百科，各模块数据互通
- **实体一键提问**：知识图谱和百科中的每个实体都支持跳转 AI 问答

### 9.3 安全设计

- 密码哈希加固存储（超长密码先 sha256 摘要再 bcrypt，`v2$` 前缀，存量哈希兼容并登录时静默升级），禁止明文传输
- JWT 双令牌：30 分钟 access token + 7 天 refresh token，前端 401 自动单飞刷新重放；登出即时失效（进程内 jti 黑名单 + token_version 版本校验）；启动时强校验 JWT_SECRET（缺失/过短/弱值拒启）
- Cypher 注入防护：全量参数化查询 + 实体标签白名单（ALLOWED_LABELS）+ 输入长度校验（已移除可能误伤合法输入的关键词黑名单）
- 登录/注册与 AI 问答接口滑动窗口限流（429 + Retry-After）
- 健康档案敏感字段（过敏史/病史/家族史）落盘前 Fernet 加密，读路径统一解密输出；JSON 文件原子写入（临时文件 + os.replace）
- CORS 白名单限制，仅允许指定前端域名访问
- API 密钥通过 `.env` 管理，`.gitignore` 防止泄露
- Markdown 渲染使用 DOMPurify 防 XSS
- 所有医疗页面底部添加免责声明

---

## 十、已知限制与未来规划

### 10.1 当前限制

| 限制项 | 说明 |
|-------|------|
| 用户数据存储 | 使用 JSON 文件持久化，生产环境应迁移至数据库 |
| DeepSeek API | 需自行申请 API 密钥，未配置时使用降级模式 |
| PDF 导出 | 健康档案 PDF 导出功能为前端占位，需集成 jsPDF |
| 知识图谱更新 | 当前为静态数据导入，不支持实时更新 |
| 外部编码缺失 | `Disease.icd10`、`Drug.atc` 当前为空串占位，待权威数据源对齐后回填 |
| 先验数据质量 | 语料 `get_prob` 量纲混杂（0~100，含特定人群发病率），诊断先验采用对数阻尼降低其影响，后续需规范化先验来源 |
| 症状覆盖缺口 | 部分症状组合（如发烧+咳嗽）在图谱中无疾病同时关联两症状，影响排序上限，受数据本身限制不做伪造 |

### 10.2 未来规划

- **数据库迁移**：用户数据迁移至 PostgreSQL / MySQL，支持高并发
- **向量检索**：集成 Embedding 模型，支持语义相似度查询
- **多模态问答**：支持图片上传（如检查报告 OCR）辅助诊断
- **实时图谱更新**：对接医疗数据源，定期增量更新知识图谱
- **移动端适配**：响应式布局优化，支持手机端访问
- **语音交互**：集成语音识别，支持语音问诊
- **用药提醒推送**：基于健康计划的定时提醒服务

---

## 十一、致谢

- [刘焕勇 / QABasedOnMedicalKnowledgeGraph](https://github.com/liuhuanyong/QABasedOnMedicalKnowledgeGraph) — 原始医疗知识图谱数据与问答框架
- [Neo4j](https://neo4j.com/) — 图数据库
- [ECharts](https://echarts.apache.org/) — 可视化图表库
- [Element Plus](https://element-plus.org/) — Vue 3 UI 组件库
- [DeepSeek](https://deepseek.com/) — 大语言模型 API

---

## 十二、免责声明

本系统仅供学习和研究使用，所提供的医疗信息仅供参考，**不能替代专业医生的诊断和治疗建议**。如有健康问题，请及时前往正规医疗机构就诊。系统开发者不对因使用本系统产生的任何后果承担责任。

---

<p align="center">医疗知识图谱智能问答与可视化系统 &copy; 2024</p>
