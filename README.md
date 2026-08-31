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
| **认证方案** | JWT + bcrypt | 7 天有效期令牌，bcrypt 密码加密 |
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

---

## 三、核心功能

### 3.1 用户认证与健康档案

- **注册/登录**：用户名唯一校验、邮箱格式验证、密码强度校验（8 位以上含字母数字）、bcrypt 加密存储
- **JWT 认证**：7 天有效期令牌，路由守卫自动拦截未登录请求
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

### 3.3 智能问答系统

- **聊天界面**：类 ChatGPT 布局，左侧历史会话列表，右侧聊天窗口
- **流式输出**：基于 Fetch + ReadableStream 实现打字机效果，支持中途停止
- **对话持久化**：聊天记录自动同步后端，支持重命名、删除、一键清空
- **DeepSeek 集成**：后端代理转发，API 密钥安全存储在服务端
- **系统提示词**：约束 AI 基于知识图谱数据回答，禁止给出用药剂量
- **答案溯源**：回答末尾列出引用的实体，点击可跳转知识图谱
- **降级方案**：未配置 API 密钥时，自动从知识图谱查询并格式化回答
- **XSS 防护**：使用 marked + DOMPurify 安全渲染 Markdown

### 3.4 多症状疾病自查

- **症状选择器**：按 8 大系统分类（呼吸/消化/神经/心血管/运动/皮肤/泌尿/全身）
- **多选支持**：支持添加症状持续时间和严重程度
- **智能推理**：基于 `has_symptom` 关系计算症状重合度，按概率排序
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
│   ├── .env                            # 环境变量（DEEPSEEK_API_KEY 等）
│   ├── requirements.txt                # Python 依赖
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
| 内存缓存 | 实体详情、总数等高频查询 5 分钟 TTL 缓存 | 减少重复数据库查询 |
| Neo4j 连接池 | `max_connection_pool_size=50` | 提升并发能力 |
| 安全加固 | 移除硬编码密钥，改用 `.env` + `python-dotenv` | 防止密钥泄露 |
| 错误处理 | `bare except` → `except Exception` + logging | 便于问题排查 |
| 时间修复 | `datetime.utcnow()` → `datetime.now(timezone.utc)` | 消除时区警告 |

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
2. 默认连接信息：`bolt://localhost:7687`，用户名 `neo4j`，密码 `12345678`
3. 导入数据：

```bash
python build_medicalgraph.py
```

### 7.3 后端配置与启动

```bash
cd backend

# 安装 Python 依赖
pip install -r requirements.txt

# 配置环境变量（创建 .env 文件）
# .env 内容示例：
# DEEPSEEK_API_KEY=sk-your-api-key-here
# NEO4J_PASSWORD=your-neo4j-password
# JWT_SECRET=your-jwt-secret

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
| **认证** | `/api/auth/register` | POST | 用户注册 |
| | `/api/auth/login` | POST | 用户登录，返回 JWT |
| **档案** | `/api/profile/get` | GET | 获取健康档案 |
| | `/api/profile/update` | POST | 更新健康档案 |
| **图谱** | `/api/kg/entities` | GET | 搜索实体（支持类型/关键词/分页） |
| | `/api/kg/entity/{name}` | GET | 实体详情（含关联症状/药品/食物/检查） |
| | `/api/kg/path` | GET | 最短路径查询 |
| | `/api/kg/related` | GET | 关联实体查询（支持深度） |
| **自查** | `/api/diagnosis` | POST | 多症状疾病匹配 |
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
- **知识图谱可视化**：ECharts 力导向布局渲染 4.4 万节点，支持缩放/拖拽/筛选/导出，悬停高亮关联节点
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

- 密码 bcrypt 加密存储，禁止明文传输
- JWT 令牌 7 天有效期，路由守卫自动拦截
- Cypher 注入防护：输入参数过滤 `;`、`//`、`DROP`、`DELETE` 等危险字符
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
