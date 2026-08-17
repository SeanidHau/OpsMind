# OpsMind：智能运维与故障诊断 Agent

> 面向秋招展示的 Agent 工程项目设计文档
>
> 技术栈：Python、LangChain、LangGraph、RAG、FastAPI、Streamlit、Qdrant、PostgreSQL、Docker Compose、LangSmith

## 1. 项目定位

OpsMind 是一个面向长流程任务的 Agent Harness，使用运维故障诊断作为可复现 benchmark。用户输入线上服务异常描述后，Harness 通过 LangGraph 编排有状态诊断流程，结合 RAG 知识库、模拟日志/指标/拓扑工具和结构化输出，完成故障分类、证据收集、根因分析、处理建议、风险判断和工单生成。

项目的核心不是普通聊天，也不是简单堆叠多个 Agent，而是展示一个可控、可解释、可评测、可恢复的 Agent Harness：

- 能规划并执行多步骤诊断流程；
- 能调用多个工具收集证据；
- 能引用知识库，避免无依据回答；
- 信息不足时主动追问；
- 工具失败时重试并降级；
- 高风险操作需要人工审批；
- 通过 Harness Loop 控制计划、上下文、动作、观察、验证和终止；
- 通过 Context Manager 管理上下文压缩、证据隔离和 Token 预算；
- 通过 Policy 和 Progress Verifier 防止越权、重复调用和无效循环；
- 通过 replay 和 trajectory evaluation 分析 Agent 的完整执行轨迹；
- 通过 LangSmith 追踪和评测质量。

### 求职目标

主要面向 AI 应用开发、大模型应用和 Python 后端岗位。

### 项目卖点

1. LangChain：模型、Prompt、Retriever、Tool、Structured Output。
2. LangGraph：状态管理、条件路由、循环、重试、持久化和人工审批。
3. Harness Loop：计划、上下文组装、模型决策、工具执行、观察、验证、重规划和终止。
4. Context Engineering：Model Context、Tool Context、Lifecycle Context 和上下文压缩。
5. RAG：文档解析、分块、向量召回、关键词召回、融合、元数据过滤和引用。
6. Agent 可靠性：证据门槛、Policy、预算、进度验证、重试、降级和 replay。
7. 工程能力：FastAPI、数据库、SSE 流式接口、Docker Compose。
8. 质量保障：固定场景、最终结果评测、trajectory 评测、消融实验和 LangSmith Trace。

## 2. 用户与首版场景

### 目标用户

企业一线运维工程师。

### 首版支持的故障类型

首版固定支持以下四类故障：

1. HTTP 5xx 错误率升高；
2. 接口响应延迟升高；
3. 数据库连接池耗尽；
4. Redis 缓存异常或命中率下降。

暂不扩展 Kubernetes、消息队列和云成本分析。

### 用户流程

用户描述故障 → Agent 分类 → 检索 Runbook、历史工单和架构文档 → 查询日志、指标、拓扑和数据库状态 → 融合证据并判断根因 → 信息不足时追问 → 生成结构化报告 → 风险检查 → 高风险动作人工审批 → 用户确认后创建模拟工单。

### 主演示案例

“订单服务接口延迟升高，根因是数据库连接池耗尽”。

演示过程：

1. 用户描述接口变慢；
2. Agent 分类为延迟问题；
3. 检索订单服务 Runbook；
4. 查询接口延迟指标；
5. 查询数据库连接池状态；
6. 发现连接池耗尽；
7. 输出证据链和处理方案；
8. 生成高风险重启建议；
9. 等待人工审批；
10. 用户批准；
11. 创建模拟工单。

该案例覆盖 RAG、多工具调用、证据融合、风险判断、Human-in-the-loop 和工单闭环。

## 3. MVP 边界

### 必须交付

- 四类预设故障场景；
- 文档上传、解析和知识库检索；
- 多轮对话和最多两轮澄清追问；
- 至少三个模拟工具；
- LangGraph 条件路由、循环、重试和降级；
- 带证据引用的结构化诊断报告；
- 高风险动作人工审批；
- 模拟工单创建；
- 至少 50 条离线评测样本；
- Docker Compose 启动方式；
- Streamlit 运维诊断工作台；
- README、架构图、评测报告和演示材料。

### 明确不做

首版禁止：

- 执行任意 Shell 命令；
- 访问真实生产服务器；
- 修改真实数据库；
- 读取项目目录外的敏感文件；
- 自动执行生产变更；
- 完整企业级 RBAC；
- 接入真实公司内部日志和工单系统。

所有工具只读或操作沙箱中的模拟数据。

## 4. 总体架构

架构数据流：

Streamlit 工作台 → FastAPI 服务层 → LangGraph Diagnosis Graph → RAG 检索层和模拟工具层 → Qdrant、PostgreSQL。

### 组件职责

| 组件 | 职责 |
|---|---|
| Python | 业务逻辑、数据模型、测试和脚本 |
| LangChain | 模型、Prompt、Retriever、Tool 和结构化输出 |
| LangGraph | Agent 状态机、路由、循环、重试、持久化和审批 |
| Agent Harness | Loop、计划、上下文、Policy、预算、验证和 replay |
| Qdrant | 文档切片向量和元数据过滤 |
| PostgreSQL | 会话、运行记录、审批、工单、评测和 checkpoint |
| FastAPI | 后端 API 与 SSE 流式事件 |
| Streamlit | 对话、执行时间线、证据和审批交互 |
| LangSmith | Trace、Prompt 调试、数据集和评测实验 |
| Docker Compose | 本地一键启动 |

## 5. Agent Harness 设计

### 5.1 Harness 的职责

模型只负责提出下一步动作，Harness 负责控制动作是否可以执行。Harness 统一管理：

- Plan：当前任务、子目标和步骤状态；
- Context：模型上下文、工具上下文、证据和历史摘要；
- Policy：权限、风险、预算、参数和重复调用检查；
- Verifier：进度、证据充分性、循环和完成条件；
- Memory：线程内状态、诊断证据和跨线程记忆；
- Checkpoint：中断、恢复、回放和故障恢复；
- Trajectory：模型决策、工具调用、观察结果和验证结果。

### 5.2 Harness Loop

每轮执行遵循以下顺序：

load_state → update_plan → build_context → call_model → parse_action → policy_check → execute_tool → record_observation → verify_progress → update_state → checkpoint → continue_or_stop

模型不能直接执行工具。所有动作必须先通过 `policy_check`。

### 5.3 核心接口

    class AgentHarness:
        async def run(self, task, config) -> RunResult: ...
        async def resume(self, thread_id, command) -> RunResult: ...
        async def replay(self, run_id, from_step=None) -> RunResult: ...

Harness 以 LangGraph 为运行时基础，自己实现核心 Loop、Context Manager、Policy、Progress Verifier、Budget Manager 和 Replay。可以增加与 Deep Agents 的对比实验，但核心实现不依赖 Deep Agents 的默认 Harness。

### 5.4 动作协议

模型只能输出结构化动作：

    class AgentAction(BaseModel):
        action_type: Literal[
            "ask_user",
            "call_tool",
            "update_plan",
            "final_answer",
            "request_approval",
            "fail"
        ]
        intent: str
        tool_name: str | None
        tool_args: dict
        expected_observation: str | None
        reason: str

Harness 对动作执行 Schema 校验、权限校验、预算校验和重复性校验。

### 5.5 事件协议

所有模型调用、工具调用和状态变化都写入统一事件流：

    class AgentEvent(BaseModel):
        run_id: str
        step_id: int
        event_type: str
        timestamp: datetime
        node: str | None
        input_summary: str | None
        action: dict | None
        observation: dict | None
        token_usage: dict | None
        latency_ms: int | None
        decision: str | None
        error: str | None

事件类型至少包括：`plan_created`、`context_built`、`model_called`、`action_proposed`、`action_blocked`、`tool_started`、`tool_finished`、`observation_recorded`、`verification_failed`、`context_compressed`、`checkpoint_saved`、`run_paused`、`run_resumed`、`run_completed` 和 `run_failed`。

### 5.6 计划和进度验证

每次诊断开始时生成显式计划。计划项状态为 `pending`、`in_progress`、`completed` 或 `blocked`。

独立的 `ProgressVerifier` 检查：

- 当前步骤是否产生新证据；
- 是否解决了一个计划项；
- 是否重复调用相同工具和参数；
- 候选根因是否收敛；
- 是否完成诊断目标；
- 是否陷入循环。

验证结果为 `progressed`、`stalled`、`regressed` 或 `completed`。连续两次 `stalled` 时触发重新规划；连续三次时强制终止。

只有在以下情况下允许重新规划：

- 新证据推翻当前故障分类；
- 关键工具不可用；
- 检索结果与当前计划不匹配；
- 当前计划连续两轮没有进展；
- 用户补充新的高优先级信息；
- 风险等级发生变化。

重新规划时保留原计划、变更原因和新的计划版本。

### 5.7 预算系统

Harness 同时管理以下预算：

- 最大执行步数；
- 最大工具调用次数；
- 最大模型调用次数；
- 最大 Token 数；
- 最大运行时间；
- 最大估算成本；
- 单工具调用预算；
- 单阶段预算。

每次动作前依次检查 `budget.remaining()` 和 `policy.check()`。预算耗尽后不再尝试新工具，进入最终总结或失败报告。

### 5.8 子 Agent 与上下文隔离

主 Agent 负责计划、调度、验证和最终输出。两个子 Agent 负责隔离大规模中间结果：

- `evidence_agent`：收集并压缩日志、指标和知识库证据；
- `diagnosis_agent`：基于压缩后的证据生成候选根因。

子 Agent 的主要目的不是增加 Agent 数量，而是限制中间结果进入主上下文。主 Agent 只接收结构化摘要和来源。

### 5.9 Replay

支持两种回放模式：

- `replay_cached`：复用历史工具结果，用于调试模型和 Prompt；
- `replay_live`：重新执行只读工具，用于验证当前系统状态。

副作用工具默认使用缓存结果。工单创建、重启计划等动作使用 idempotency key，防止回放产生重复操作。

## 6. Harness 上下文管理

### 6.1 上下文分层

| 层级 | 内容 | 生命周期 |
|---|---|---|
| Task Context | 当前任务、目标、限制 | 单次运行 |
| Working Context | 当前计划、候选根因、最近观察 | 当前线程 |
| Evidence Context | 日志、指标、文档证据摘要 | 当前诊断 |
| Long-term Memory | 历史故障模式和用户偏好 | 跨线程 |

每次调用模型时，只将当前步骤需要的最小上下文放入 Prompt。完整原始日志保存在外部存储，模型上下文只保留摘要、来源和关键字段。

### 6.2 压缩和淘汰策略

- 上下文达到模型窗口 60%：开始压缩；
- 达到 75%：淘汰低价值工具结果；
- 达到 85%：只保留任务、计划、关键证据和最近观察；
- 达到 95%：停止新工具调用，进入降级总结。

压缩结果必须保留原始数据来源、关键事实、已验证结论、未解决问题和下一步建议。

### 6.3 生命周期 Middleware

实现以下最小 Middleware：

- `before_model`：注入当前任务、计划和最小证据集；
- `after_model`：解析动作，更新预算和轨迹；
- `before_tool`：检查权限、参数和风险；
- `after_tool`：截断结果，提取证据，写入观察记录；
- `before_context`：压缩历史并移除低价值内容。

## 7. LangGraph 工作流

### 节点

只将具有独立状态、重试策略或路由意义的步骤做成节点：

classify → retrieve → collect_evidence → analyze → check_completeness → generate_report → risk_check → human_approval → create_ticket

### 状态对象

    class DiagnosisState(TypedDict):
        session_id: str
        thread_id: str
        run_id: str
        user_query: str
        conversation: list
        issue_type: str | None
        service_name: str | None
        severity: str | None
        plan: list
        plan_version: int
        context_refs: list
        budget: dict
        trajectory: list
        progress_status: str | None
        retrieved_documents: list
        tool_results: list
        evidence: list
        hypotheses: list
        missing_information: list
        diagnosis: dict | None
        recommended_actions: list
        approval_request: dict | None
        ticket: dict | None
        retry_count: int
        question_count: int
        tool_call_count: int
        step_count: int
        errors: list

所有节点只通过 State 读写数据，不依赖隐式全局变量。

### 路由与失败处理

- 分类置信度低于阈值：进入澄清节点，最多追问两轮；
- 检索结果为空：标记知识不足，进入工具查询或降级报告；
- 关键工具失败：重试两次，仍失败则生成部分诊断并列出缺失证据；
- 证据不足：不得输出确定性根因，只能输出高概率原因或待验证假设；
- 高风险操作：进入 interrupt，保存 checkpoint，等待人工处理；
- 达到任意执行上限：进入降级节点，输出当前可得结果。

### 全局限制

| 限制 | 上限 |
|---|---:|
| 图执行步数 | 15 |
| 工具调用次数 | 8 |
| 澄清追问次数 | 2 |
| 单工具自动重试次数 | 2 |
| 总耗时 | 60 秒 |

## 8. RAG 设计

### 知识库内容

首版准备以下 Markdown 文档：

- 服务架构与依赖说明；
- API 接口文档；
- 数据库与 Redis 运维手册；
- 故障排查 Runbook；
- 历史故障复盘文档。

每篇文档包含 service、component、environment、version、severity、document_type、updated_at 等元数据。

### Ingestion Pipeline

扫描文档 → 计算文件 hash → 解析与分块 → 生成 metadata → 生成 embedding → 写入 Qdrant → 删除旧版本切片 → 输出索引统计。

要求重复执行幂等，不产生重复向量，并支持按文档版本回滚。

### 检索流程

问题改写 → 查询分类 → Qdrant 向量召回 → BM25 关键词召回 → 合并去重 → Reciprocal Rank Fusion → metadata filter → Top-K 证据 → 返回来源和片段。

第一版实现向量检索、BM25、RRF、metadata filter 和文档来源引用。Reranker 作为增强项，不阻塞 MVP。

默认 Embedding 使用本地 BAAI/bge-m3 或同类中文模型，并通过配置支持替换 API Embedding。

### 证据门槛

允许输出诊断结论前至少满足：

- 至少一条相关知识库证据；
- 至少一个日志或指标工具结果；
- 根因和证据之间存在可解释关联。

报告必须区分：

- 已确认事实；
- 高概率原因；
- 待验证假设。

## 9. 模拟工具

第一版工具只操作预先设计的故障场景数据：

- query_logs：查询指定服务和时间窗口的日志；
- query_metrics：查询错误率、延迟、吞吐量和缓存命中率；
- query_topology：查询服务依赖关系；
- query_db_pool：查询数据库连接池状态；
- create_ticket：创建模拟工单；
- generate_restart_plan：生成模拟重启计划，不实际执行重启。

### 工具失败策略

- 每个工具最多自动重试两次；
- 使用指数退避；
- 记录错误原因；
- 必要时尝试替代工具；
- 关键工具持续失败时生成部分诊断；
- 报告明确列出缺失证据和置信度下降原因。

## 10. 预设场景数据模型

每个场景包含固定的日志、指标、依赖、标准根因和推荐动作，保证演示与评测可复现。

    {
      "scenario_id": "api_timeout_001",
      "service": "order-service",
      "time_range": {
        "start": "2026-08-14T10:00:00",
        "end": "2026-08-14T10:15:00"
      },
      "logs": [],
      "metrics": [],
      "dependencies": [],
      "ground_truth": {
        "root_cause": "database connection pool exhausted",
        "severity": "P1",
        "actions": []
      }
    }

建议每类故障至少准备 3 个场景：正常场景、信息缺失场景和证据冲突或工具异常场景。

## 11. 结构化诊断报告

    class DiagnosisReport(BaseModel):
        summary: str
        severity: Literal["P0", "P1", "P2", "P3"]
        impact_scope: str
        confirmed_facts: list[str]
        probable_causes: list[str]
        hypotheses_to_verify: list[str]
        evidence: list[Evidence]
        investigation_steps: list[str]
        recommended_actions: list[Action]
        risk_level: Literal["low", "medium", "high"]
        requires_approval: bool
        references: list[Reference]

解析失败时自动重试一次，随后使用修复型 Parser；仍然失败则返回错误状态，不将非结构化文本当作成功结果。

报告包含：事件摘要、故障等级、影响范围、可能原因、关键证据、排查过程、建议操作、风险说明、是否需要人工审批和参考文档。

## 12. 风险与人工审批

| 风险等级 | 示例 | 是否审批 |
|---|---|---|
| Low | 查询日志、查询指标、生成排查建议 | 否 |
| Medium | 创建工单、扩大日志采样范围 | 可配置 |
| High | 重启服务、修改配置、数据库变更 | 必须审批 |

高风险动作进入 LangGraph interrupt，使用 checkpoint 保存状态。审批结果支持：

- approve：按原方案执行模拟动作；
- edit：修改动作参数后继续；
- reject：拒绝动作并记录反馈。

创建工单前必须同时满足：

- 已生成结构化诊断报告；
- 诊断报告通过风险检查；
- 用户确认创建工单；
- 必要字段完整。

工单字段包括 ticket_id、title、description、service、severity、root_cause、evidence、recommended_actions、assignee、status、created_at。

## 13. 数据存储

### PostgreSQL

存储用户和开发模式身份、会话、消息、运行记录、审批请求、审批结果、审计日志、模拟工单、评测样本、评测结果和 LangGraph checkpoint。

### Qdrant

存储文档切片向量、文档来源、文档版本以及服务、组件、环境、故障等级等 metadata。

### 会话恢复

每次诊断绑定 session_id、thread_id 和 run_id。用户关闭页面后，可以通过 thread_id 重新打开会话；人工审批中断后，可以继续批准、修改或拒绝。

## 14. API 设计

    POST /api/v1/chat
    POST /api/v1/sessions
    GET  /api/v1/sessions/{session_id}
    GET  /api/v1/runs/{run_id}
    GET  /api/v1/runs/{run_id}/events
    POST /api/v1/runs/{run_id}/replay
    POST /api/v1/threads/{thread_id}/resume
    POST /api/v1/approvals/{approval_id}
    POST /api/v1/knowledge/documents
    GET  /api/v1/scenarios

聊天接口使用 SSE 返回事件：

run_started、node_started、tool_called、tool_finished、evidence_found、approval_required、run_finished、run_failed。

Streamlit 依据事件更新 Agent 执行时间线、工具结果、证据和审批卡片。

## 15. 前端工作台

界面定位为“运维诊断工作台”，不是普通聊天页面。

- 左侧：会话列表和故障场景选择器；
- 中间：对话、诊断进度和最终报告；
- 右侧：Agent 执行时间线、证据、工具结果和系统状态；
- 审批卡片：展示操作内容、风险等级和批准/拒绝按钮。

MVP 使用 Streamlit；如果时间充足，再替换为 React + TypeScript。

## 16. 模型与配置

模型接口采用 LangChain 标准抽象，默认支持 OpenAI-compatible API、DeepSeek 和 Ollama 本地模型。通过环境变量切换模型供应商，不把供应商写死在业务代码中。

建议配置项：

    LLM_PROVIDER
    LLM_MODEL
    LLM_BASE_URL
    EMBEDDING_PROVIDER
    EMBEDDING_MODEL
    QDRANT_URL
    POSTGRES_DSN
    LANGSMITH_TRACING
    LANGSMITH_API_KEY

## 17. 项目目录

    OpsMind/
    ├── app/
    │   ├── api/              # FastAPI 路由
    │   ├── harness/          # Harness Loop、预算、Policy、验证和 replay
    │   ├── context/          # 上下文分层、压缩和摘要
    │   ├── graph/            # LangGraph 状态与节点
    │   ├── agents/           # Agent 提示词与调度逻辑
    │   ├── tools/            # 日志、指标、拓扑、工单工具
    │   ├── rag/              # 文档处理、检索、重排
    │   ├── models/           # Pydantic 数据模型
    │   ├── repositories/     # 数据库访问
    │   └── config.py
    ├── data/
    │   ├── knowledge/
    │   ├── scenarios/
    │   └── eval/
    ├── frontend/
    ├── tests/
    ├── scripts/
    ├── docker-compose.yml
    ├── pyproject.toml
    └── README.md

核心模块是 harness、context、graph、rag、tools 和 models。

## 18. 测试与评测

### 四层测试

1. 单元测试：文档切分、检索、解析器、风险判断和重试逻辑；
2. 工具测试：日志、指标、拓扑、数据库状态和工单工具；
3. Graph 测试：正常路径、追问路径、审批路径和失败路径；
4. 评测测试：50 条样本验证分类、引用、根因和报告质量。

Graph 测试使用固定模型输出或 Mock，避免测试依赖真实模型随机性。

### 评测数据集

至少 50 条样本，每类故障 10～15 条，包含：

- 信息完整的问题；
- 缺少关键信息、应触发追问的问题；
- 包含误导性证据的问题；
- 工具超时或返回空结果的问题；
- 知识库没有相关内容的问题。

### 指标目标

| 指标 | 目标 |
|---|---:|
| 故障分类准确率 | ≥ 90% |
| 根因判断准确率 | ≥ 80% |
| 关键证据引用正确率 | ≥ 90% |
| 结构化报告成功率 | ≥ 95% |
| 工具调用成功率 | ≥ 95% |
| 高风险动作误执行率 | 0% |
| 无证据强行下结论比例 | ≤ 5% |

代码评测负责分类、根因、来源引用、JSON Schema、审批触发和调用上限。LLM-as-judge 或人工评测负责分析完整性、证据一致性、排查步骤合理性和表达清晰度。

使用 LangSmith 记录 Trace、比较实验版本，并将失败样本加入评测集。

### Harness 对比实验

至少实现三组配置：

| 实验 | 配置 |
|---|---|
| Baseline | 普通 LangGraph Agent，不使用 Context Manager 和 Progress Verifier |
| Harness-1 | 加入预算、Policy 和统一事件流 |
| Harness-2 | 在 Harness-1 基础上加入 Context Compression、Replan 和 Replay |

比较以下指标：

- 最终诊断正确率；
- trajectory 成功率；
- 平均工具调用数；
- 重复调用率；
- 平均 Token 消耗；
- 上下文长度；
- 超时率；
- 高风险动作拦截率。

### 必须展示的失败案例

- 知识库没有相关内容；
- 日志与指标互相矛盾；
- 工具调用超时；
- 模型输出格式错误；
- 用户拒绝高风险操作；
- 用户提供信息不足；
- Agent 达到最大步数。

## 19. 部署方式

Docker Compose 服务：

- frontend；
- backend；
- postgres；
- qdrant。

模型通过环境变量接入外部 API；配置 Ollama 时可切换为本地模型。

目标启动方式：

    docker compose up -d
    python scripts/ingest.py
    streamlit run frontend/app.py

## 20. 4～6 周开发计划

Harness 版本建议按 4～6 周安排。如果必须在 2～4 周内完成，应优先保留 Harness Core、一个完整故障 benchmark 和对比评测，缩减前端包装及故障场景数量。

### 第 1 周：数据与基础能力

初始化 Python 项目和依赖；定义 Pydantic 数据模型；设计四类故障场景；编写模拟日志、指标、拓扑和数据库数据；实现模拟工具层；定义 AgentAction、AgentEvent、PlanItem 和 Budget；编写基础单元测试。

### 第 2 周：RAG 与单次诊断

编写知识库文档；实现 ingestion pipeline；接入 Qdrant 和 BM25；实现 RRF 融合和来源引用；实现 Harness Loop、Context Manager、Policy 和 Progress Verifier；实现 classify、retrieve、collect、analyze、report 节点；打通单次诊断闭环。

### 第 3 周：可靠性与接口

加入多轮追问、工具重试、超时、降级、Replan、Context Compression、Replay、风险检查、人工审批、PostgreSQL checkpoint、FastAPI、SSE 和模拟工单。

### 第 4 周：展示与评测

实现 Streamlit 工作台；准备 50 条评测样本；接入 LangSmith Trace 和评测；完善失败案例；编写 Docker Compose、README 和架构图；录制 3～5 分钟演示视频；整理简历项目描述和面试问答。

## 21. 秋招简历描述

> 基于 Python、LangChain、LangGraph 和 RAG 构建企业级智能运维 Agent，支持故障分类、混合检索、日志/指标/拓扑查询、证据融合、结构化诊断、风险分级和人工审批。通过 LangGraph 实现有状态工作流、条件路由、工具重试、降级和 checkpoint 恢复；使用 Qdrant + BM25 实现带来源引用的混合检索，并基于 LangSmith 构建 50+ 条评测集，验证分类、根因判断、证据引用和高风险操作拦截能力。

实际指标必须在完成评测后填写，不应预先虚构结果。

## 22. 面试重点问题

### 为什么使用 LangGraph，而不是一个普通 Agent？

故障诊断包含明确的状态、条件路由、循环、重试和人工审批。LangGraph 可以将这些步骤显式建模，并通过 checkpoint 支持中断和恢复，流程更容易解释、测试和控制。

### 为什么需要 RAG？

故障诊断依赖服务架构、Runbook、历史复盘和版本信息。RAG 可以将回答绑定到项目知识库并提供引用，降低模型凭记忆编造处理方案的风险。

### 如何避免 Agent 无限调用工具？

设置图步数、工具调用次数、追问次数、重试次数和总耗时上限；超过任一限制后进入降级节点，输出当前可得证据和限制说明。

### 如何判断诊断是否可靠？

使用显式证据门槛：至少一条相关知识库证据和一个日志或指标结果，并要求结论与证据存在可解释关联；无法满足时只输出待验证假设。

### 为什么高风险操作必须人工审批？

重启服务、修改配置和数据库变更可能造成二次故障。系统通过 LangGraph interrupt 暂停执行并保存状态，用户可批准、修改或拒绝，形成可审计的 Human-in-the-loop 流程。

### 如何评测 RAG 和 Agent？

用固定场景和 50 条样本评测分类、根因、引用、结构化输出、工具调用和审批触发；对分析完整性和证据一致性使用 LLM-as-judge 或人工评测，并通过 LangSmith 观察每一步 Trace。

## 23. 完成定义

当以下条件全部满足时，OpsMind 的 MVP 才算完成：

- 用户可以从 Streamlit 发起一轮故障诊断；
- Agent 能走通分类、检索、工具查询、分析和报告生成；
- Harness 能完成计划、上下文组装、动作校验、工具执行、观察、进度验证和终止；
- 至少一个案例能触发人工审批并恢复执行；
- 至少一个案例支持从 checkpoint replay，并能复用缓存工具结果；
- 报告包含证据和来源引用；
- 工具失败和信息不足路径可解释、可测试；
- 50 条评测样本可以自动运行；
- Docker Compose 可以启动完整依赖；
- README 能让面试官在本地复现；
- Baseline 与 Harness 至少完成一组对比评测；
- 有成功案例、失败案例、架构图和演示视频。
