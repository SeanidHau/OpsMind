"""LangChainActionProvider 的验收测试。"""

import json
from typing import Any

import pytest
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.action_provider import LangChainActionProvider
from app.harness.loop import create_initial_state
from app.models.contracts import (
    ActionType,
    AgentAction,
    BudgetState,
    ContextItem,
    ContextSnapshot,
    ContextSource,
    ModelInvocation,
    PlanItem,
    PlanStatus,
    ToolDefinition,
    ToolRiskLevel,
)
from tests.support import diagnosis_report


class FakeStructuredRunnable:
    """记录 LangChain 消息并返回可控结构化动作。"""

    def __init__(self, response: AgentAction | dict[str, Any]) -> None:
        self._response = response
        self.inputs: list[list[BaseMessage]] = []

    async def ainvoke(self, input: list[BaseMessage]) -> AgentAction | dict[str, Any]:
        """保存输入消息，模拟异步 Runnable 调用。"""
        self.inputs.append(input)
        return self._response


class FakeChatModel:
    """模拟支持 `with_structured_output` 的 LangChain 聊天模型。"""

    def __init__(self, response: AgentAction | dict[str, Any]) -> None:
        self.schema: type[AgentAction] | None = None
        self.include_raw: bool | None = None
        self.method: str | None = None
        self.runnable = FakeStructuredRunnable(response)

    def with_structured_output(
        self,
        schema: type[AgentAction],
        *,
        include_raw: bool = False,
        method: str | None = None,
    ) -> FakeStructuredRunnable:
        """记录绑定的 Pydantic schema 并返回结构化 Runnable。"""
        self.schema = schema
        self.include_raw = include_raw
        self.method = method
        return self.runnable


class FakeRawResponse:
    """模拟携带 LangChain 用量元数据的原始模型响应。"""

    def __init__(
        self,
        *,
        usage_metadata: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata


def make_state() -> dict[str, Any]:
    """构造包含最小模型上下文的 Harness 状态。"""
    state = create_initial_state(
        session_id="session-provider",
        thread_id="thread-provider",
        user_query="支付服务请求超时",
        budget=BudgetState(
            max_steps=5,
            max_tool_calls=3,
            max_model_calls=3,
            max_tokens=1_000,
            max_runtime_seconds=60,
            max_estimated_cost_usd=1.0,
        ),
    )
    state["model_context"] = ContextSnapshot(
        items=[
            ContextItem(
                source=ContextSource.TASK,
                reference="user_query",
                content="支付服务请求超时",
                priority=100,
            ),
            ContextItem(
                source=ContextSource.EVIDENCE,
                reference="evidence:abc",
                content='{"error_rate":0.12}',
                priority=70,
            ),
        ],
        total_chars=28,
        truncated=False,
    )
    return state


@pytest.mark.asyncio
async def test_provider_binds_agent_action_schema_and_uses_model_context() -> None:
    """模型必须绑定 `AgentAction`，且只接收最小上下文而非完整状态。"""
    model = FakeChatModel(
        AgentAction(
            action_type=ActionType.CALL_TOOL,
            intent="查询指标",
            tool_name="query_metrics",
            tool_args={"service": "payment-service"},
            reason="需要确认错误率和延迟。",
        )
    )
    provider = LangChainActionProvider(model)

    invocation = await provider.propose_action(make_state())

    assert isinstance(invocation, ModelInvocation)
    assert invocation.action.action_type is ActionType.CALL_TOOL
    assert invocation.usage.total_tokens == 0
    assert model.schema is AgentAction
    assert model.include_raw is True
    assert model.method is None
    messages = model.runnable.inputs[0]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)

    payload = json.loads(str(messages[1].content))
    assert payload["user_query"] == "支付服务请求超时"
    assert payload["context"][1]["reference"] == "evidence:abc"
    assert payload["plan_version"] == 0
    assert payload["replan_requested"] is False
    assert payload["replan_reason"] is None
    assert payload["replan_feedback"] is None
    assert payload["replan_correction_count"] == 0
    assert payload["replan_count"] == 0
    assert payload["question_count"] == 0
    assert payload["runnable_plan_item_ids"] == []
    assert payload["evidence_ids"] == []
    assert "budget" not in payload
    assert "trajectory" not in payload
    assert "final_answer 必须携带 report" in str(messages[0].content)
    assert "evidence:<evidence_id>" in str(messages[0].content)
    assert "update_plan" in str(messages[0].content)
    assert "replan_feedback" in str(messages[0].content)
    assert "runnable_plan_item_ids" in str(messages[0].content)


@pytest.mark.asyncio
async def test_provider_exposes_only_pending_plan_items_with_ready_dependencies() -> None:
    """模型只能收到尚未执行且前置条件已满足的计划项。"""
    model = FakeChatModel(
        AgentAction(
            action_type=ActionType.ASK_USER,
            intent="补充影响范围",
            reason="当前证据不足。",
            question="受影响的接口有哪些？",
        )
    )
    state = make_state()
    completed = PlanItem(
        title="已完成步骤",
        rationale="测试状态过滤。",
        status=PlanStatus.COMPLETED,
    )
    runnable = PlanItem(title="可执行步骤", rationale="依赖已经完成。", depends_on=[completed.id])
    blocked = PlanItem(title="不可执行步骤", rationale="依赖尚未完成。")
    dependent = PlanItem(
        title="等待依赖步骤",
        rationale="依赖尚未完成。",
        depends_on=[blocked.id],
    )
    state["plan"] = [completed, runnable, blocked, dependent]
    state["plan_version"] = 1

    await LangChainActionProvider(model).propose_action(state)  # type: ignore[arg-type]

    payload = json.loads(str(model.runnable.inputs[0][1].content))
    assert payload["runnable_plan_item_ids"] == [str(runnable.id), str(blocked.id)]


def test_provider_can_use_function_calling_for_compatible_endpoints() -> None:
    """兼容端点可显式选择函数调用结构化输出协议。"""
    model = FakeChatModel({"action_type": "ask_user", "question": "影响范围是什么？"})

    LangChainActionProvider(model, structured_output_method="function_calling")

    assert model.method == "function_calling"


@pytest.mark.asyncio
async def test_provider_validates_dictionary_response_against_action_contract() -> None:
    """部分模型返回字典时，提供器仍必须执行 Pydantic 动作校验。"""
    model = FakeChatModel(
        {
            "action_type": "final_answer",
            "intent": "输出当前结论",
            "reason": "证据已足够。",
            "report": diagnosis_report().model_dump(mode="json"),
        }
    )

    invocation = await LangChainActionProvider(model).propose_action(make_state())

    assert invocation.action.action_type is ActionType.FINAL_ANSWER


@pytest.mark.asyncio
async def test_provider_extracts_raw_usage_and_calculates_cost() -> None:
    """Provider 应从原始响应提取 Token，并按注入价格估算成本。"""
    raw_response = FakeRawResponse(
        usage_metadata={"input_tokens": 120, "output_tokens": 30},
    )
    model = FakeChatModel(
        {
            "raw": raw_response,
            "parsed": AgentAction(
                action_type=ActionType.CALL_TOOL,
                intent="查询指标",
                tool_name="query_metrics",
                tool_args={"service": "payment-service"},
                reason="收集诊断证据。",
            ),
            "parsing_error": None,
        }
    )
    provider = LangChainActionProvider(
        model,
        input_cost_per_1k_tokens=0.01,
        output_cost_per_1k_tokens=0.02,
    )

    invocation = await provider.propose_action(make_state())

    assert invocation.usage.input_tokens == 120
    assert invocation.usage.output_tokens == 30
    assert invocation.usage.total_tokens == 150
    assert invocation.usage.estimated_cost_usd == pytest.approx(0.0018)


@pytest.mark.asyncio
async def test_provider_rejects_state_without_built_context() -> None:
    """Harness 未构建 Context 时，不允许模型读取完整状态并自行决策。"""
    model = FakeChatModel(
        {
            "action_type": "final_answer",
            "intent": "输出结论",
            "reason": "测试。",
        }
    )
    state = make_state()
    state["model_context"] = None

    with pytest.raises(ValueError, match="model_context"):
        await LangChainActionProvider(model).propose_action(state)


@pytest.mark.asyncio
async def test_provider_exposes_registered_tool_contracts_to_the_model() -> None:
    """模型上下文只应包含可调用工具的名称、说明和参数约束。"""
    model = FakeChatModel(
        AgentAction(
            action_type=ActionType.CALL_TOOL,
            intent="检索故障处理知识",
            tool_name="query_knowledge",
            tool_args={"query": "支付超时"},
            reason="需要参考 Runbook。",
        )
    )
    provider = LangChainActionProvider(
        model,
        tools=(
            ToolDefinition(
                name="query_knowledge",
                description="检索运维知识库。",
                risk_level=ToolRiskLevel.LOW,
                read_only=True,
                required_args=("query",),
                allowed_args=("query", "service"),
            ),
        ),
    )

    await provider.propose_action(make_state())

    payload = json.loads(str(model.runnable.inputs[0][1].content))
    assert payload["available_tools"] == [
        {
            "name": "query_knowledge",
            "description": "检索运维知识库。",
            "required_args": ["query"],
            "allowed_args": ["query", "service"],
        }
    ]
