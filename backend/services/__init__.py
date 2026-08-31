# coding: utf-8
"""
GraphRAG 问答管线服务包（阶段三）。

模块职责划分：
- graph_db:           Neo4j 连接与 Cypher 执行工具（含只读超时事务）
- entity_recognizer:  基于 dict/*.txt 词典的实体识别（含别名归一化与否定过滤）
- emergency:          急症红牌检测（最高优先级，命中即终止问答流程）
- intent_router:      规则+词典意图路由（零 LLM 调用）
- vector_index:       纯 Python 哈希字符 n-gram 嵌入 + Neo4j 原生向量索引
- retriever:          关键词+向量双路混合检索与图谱三元组抽取
- text2cypher:        长尾问题的 Text2Cypher（白名单校验 + 只读超时执行）
- llm_client:         DeepSeek 流式/一次性调用共享封装
- diagnosis_service:  /api/diagnosis 加权诊断核心逻辑（路由与问答管线复用）
- drug_service:       用药安全核心逻辑（路由与问答管线复用）
- rag_pipeline:       问答主管线（SSE 帧生成：emergency/intent/content/sources）
"""
