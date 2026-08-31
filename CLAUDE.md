# 医疗知识图谱问答系统

## 项目概述

基于医疗知识图谱的智能问答与可视化系统，提供疾病自查、用药安全、健康计划、就医指南等功能。

- **知识库规模：** 4.4 万实体 + 30 万关系
- **AI 引擎：** DeepSeek 大模型（SSE 流式输出）
- **图数据库：** Neo4j

## 技术栈

### 前端 (`frontend/`)

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue 3 | Composition API (`<script setup>`) | 框架 |
| Vite | 8.x | 构建工具 |
| Element Plus | 2.14.x | UI 组件库（自动导入） |
| Tailwind CSS | 3.x | 原子化 CSS |
| Pinia | 3.x | 状态管理 |
| Vue Router | 4.x | 路由（history 模式） |
| ECharts | 6.x | 知识图谱力导向图（按需引入） |
| Axios | - | HTTP 请求 |
| marked + DOMPurify | - | 安全 Markdown 渲染 |
| Plus Jakarta Sans | Google Fonts | 标题字体 |

### 后端 (`backend/`)

| 技术 | 用途 |
|------|------|
| Python FastAPI | API 服务（端口 8000） |
| Neo4j | 图数据库 |
| DeepSeek | LLM 问答引擎 |
| python-dotenv | 环境变量管理 |

## 环境配置

后端通过 `.env` 文件管理敏感配置（已在 `.gitignore` 中排除）：

```env
DEEPSEEK_API_KEY=sk-xxx
NEO4J_PASSWORD=your-password
JWT_SECRET=your-secret
```

## 常用命令

```bash
# 前端开发
cd frontend
npm install          # 安装依赖
npm run dev          # 启动开发服务器 → http://localhost:5173
npm run build        # 生产构建

# 后端
cd backend
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## 设计系统（方案 C「空间沉浸」）

3D 景深 + 浮动层 + 玻璃态导航，灵感来源 Apple Vision Pro 空间界面。

### 配色

| 用途 | 色值 | Tailwind 类 |
|------|------|-------------|
| 主色（靛蓝） | `#4F46E5` | `primary-500` |
| 强调色（蓝紫） | `#7C3AED` | `accent-500` |
| 成功/健康绿 | `#10B981` | `success-500` |
| 危险/警告红 | `#F43F5E` | `danger-500` |
| 背景渐变 | `#F0F4FF → #E8ECF4` | linear-gradient 135deg |
| 卡片背景 | `rgba(255,255,255,0.72)` | glass-card backdrop-blur |
| 正文 | `#0F172A` | `surface-900` |
| 次要文字 | `#475569` | `surface-600` |

### 字体

- **标题：** Plus Jakarta Sans（几何感，font-display）
- **正文：** Inter / Noto Sans SC
- **数据/代码：** JetBrains Mono

### 关键设计规范

- **导航：** 顶部浮动胶囊栏（`border-radius: 999px`，毛玻璃 `backdrop-blur-xl`，`sticky top-3` 居中）
- **卡片：** `.glass-card` 半透明毛玻璃（`bg-white/72 + blur 20px + border rgba(255,255,255,0.3)`），圆角 16px
- **3D 卡片：** `.float-card` 带 `perspective(1200px) rotateX(2deg)` 透视 + 多层阴影模拟景深
- **背景：** 渐变底色 + SVG 网格纹理（CSS `grid-drift` 20s 缓慢漂移）+ 环境光晕装饰
- **按钮：** 主色渐变（`primary → accent`）+ 发光阴影 + 弹性 hover（`cubic-bezier(0.34,1.56,0.64,1)`）
- **页面切换：** `page-spatial` 3D 旋转过渡（`rotateY(6deg) + scale(0.97)` 入场）
- **输入框：** 毛玻璃背景 + focus 靛蓝外发光
- **表格：** 表头半透明靛蓝背景，行 hover 微妙高亮
- **抽屉：** body 区域 `overflow-y: auto` + flex 布局，内容过长时可滚动
- **工具类：** `.gradient-text`（渐变文字）、`.glow-border`（发光边框）、`.spatial-btn`（空间按钮）

### 实体类型配色（知识图谱）

| 类型 | 色值 |
|------|------|
| 疾病 | `#e74c3c` |
| 药品 | `#3498db` |
| 症状 | `#2ecc71` |
| 食物 | `#f39c12` |
| 检查 | `#9b59b6` |
| 科室 | `#e67e22` |
| 在售药品 | `#95a5a6` |

## 路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/login` | LoginView | 登录（无需认证） |
| `/register` | RegisterView | 注册（无需认证） |
| `/kg` | KnowledgeGraphView | 知识图谱可视化（默认首页，支持 `?entity=` 参数） |
| `/chat` | ChatView | AI 智能问答（支持 `?q=` 参数预填输入） |
| `/diagnosis` | DiagnosisView | 症状→疾病自查 |
| `/drug` | DrugSafetyView | 用药安全查询 |
| `/health` | HealthPlanView | 健康管理计划 |
| `/guide` | MedicalGuideView | 就医指南 |
| `/wiki` | WikiView | 知识百科 |
| `/profile` | ProfileView | 个人健康档案 |

## API 代理

Vite 开发服务器将 `/api` 请求代理到 `http://localhost:8000`，SSE 请求禁用缓冲（`X-Accel-Buffering: no`）。

## 注意事项

- 所有 API 请求自动携带 JWT `Authorization: Bearer` 头
- 401 响应自动跳转登录页
- Element Plus 组件通过 `unplugin-vue-components` 自动导入，无需手动 import
- ECharts 仅引入 GraphChart 所需模块（`echarts/core`），非全量引入
- Element Plus 图标在 `main.js` 中按需注册（~32 个），非全量注册
- Tailwind 自定义色系在 `tailwind.config.js` 中定义，全局 CSS 变量在 `style.css` 中同步
- `stores/index.js` 中 `userInfo` / `profile` 使用 `shallowRef` 优化
- 路由查询参数：`/kg?entity=感冒` 定位实体，`/chat?q=问题` 预填输入
- 实体详情抽屉支持"咨询 AI"按钮，自动生成对应类型提问
- 用户健康档案为 `null` 时，后端使用 `body.get("profile") or default` 防止 `NoneType` 错误
- JSON 数据文件：`users.json`、`profiles.json`、`health_records.json`、`health_plans.json`、`chat_history.json`
- AI 生成的健康计划自动保存到 `health_plans.json`，前端「计划历史」tab 可回看和一键清理
