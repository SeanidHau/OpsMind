"""基于 LangChain 结构化输出的动作提供器。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.models.contracts import (
    AgentAction,
    DiagnosisState,
    ModelInvocation,
    ModelUsage,
    PlanStatus,
    ToolDefinition,
)


class ActionRunnable(Protocol):
    """能够异步返回结构化动作的 LangChain Runnable。"""

    async def ainvoke(self, input: list[BaseMessage]) -> AgentAction | dict[str, Any]:
        """接收消息列表并返回模型结构化输出。"""


class StructuredActionChatModel(Protocol):
    """支持 Pydantic Structured Output 的 LangChain 聊天模型。"""

    def with_structured_output(
        self,
        schema: type[AgentAction],
        **kwargs: Any,
    ) -> ActionRunnable:
        """将聊天模型绑定到指定 schema，并可保留原始响应。"""


class LangChainActionProvider:
    """仅基于最小模型上下文，让 LangChain 模型提出下一步动作。"""

    _SYSTEM_INSTRUCTION = """\
    你是 OpsMind 的诊断规划模型。只能提出符合 AgentAction schema 的下一步动作。
    不要声称执行过工具；工具执行由 Harness 完成。
    证据不足时，选择 call_tool 或 ask_user。
    当选择 ask_user 时，必须在 question 字段中提出一个明确、可回答的问题。
    只有当前上下文无法支持安全诊断时才追问，不要将多个独立问题合并为一次 ask_user。

    当 plan_version 为 0 时，先选择 update_plan，并提交 2 到 5 个可执行计划项。
    当 plan_version 大于 0 时，runnable_plan_item_ids 是唯一可绑定工具或追问动作的
    计划项列表。不得引用列表外的计划项。若该列表为空且 evidence_ids 非空，必须选择
    final_answer 汇总证据；不要重复调用工具或更新计划。
    执行 call_tool 或 ask_user 时，优先使用 context 中 plan:<UUID> 形式的 reference
    填写 plan_item_id，使 Harness 能校验依赖和执行状态。
    当 replan_requested 为 true 时，必须先选择 update_plan，说明新证据或停滞原因，
    再选择新的工具路径。只有 update_plan 可以携带 plan 字段。
    replan_feedback 不为空时，上一条动作已被 Harness 拒绝。
    若反馈指出计划项已经 completed、工具已成功执行或工具达到调用上限，不得再次
    调用该工具：应选择其他 pending 计划项；若没有待执行计划且证据足够，直接选择
    final_answer。其他反馈必须先选择 update_plan；不要重复被拒绝的工具或最终回答动作。

    只有证据足够时才选择 final_answer。final_answer 必须携带 report。
    final_answer 不得携带 plan_item_id。
    report 的摘要、候选根因和建议必须基于当前 context；
    report.evidence_ids 只能引用 context 中形如 evidence:<evidence_id> 的条目，
    并且写入时应去掉 evidence: 前缀。
    不要引用未出现在 context 中的证据 ID。

    选择 call_tool 时，只能使用 available_tools 中的 name，并且 tool_args 必须符合
    对应工具的 required_args 和 allowed_args。
    """

    def __init__(
        self,
        chat_model: StructuredActionChatModel,
        *,
        input_cost_per_1k_tokens: float = 0.0,
        output_cost_per_1k_tokens: float = 0.0,
        tools: tuple[ToolDefinition, ...] = (),
        structured_output_method: Literal["function_calling", "json_mode", "json_schema"]
        | None = None,
    ) -> None:
        """绑定结构化动作模型和可选的 Token 价格配置。"""
        if input_cost_per_1k_tokens < 0:
            raise ValueError("input_cost_per_1k_tokens must not be negative")
        if output_cost_per_1k_tokens < 0:
            raise ValueError("output_cost_per_1k_tokens must not be negative")

        self._input_cost_per_1k_tokens = input_cost_per_1k_tokens
        self._output_cost_per_1k_tokens = output_cost_per_1k_tokens
        self._tools = tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "required_args": tool.required_args,
                "allowed_args": tool.allowed_args,
            }
            for tool in tools
        )
        # include_raw=True 用于从 AIMessage 中提取供应商实际用量。
        self._action_runnable = (
            chat_model.with_structured_output(
                AgentAction,
                include_raw=True,
                method=structured_output_method,
            )
            if structured_output_method is not None
            else chat_model.with_structured_output(AgentAction, include_raw=True)
        )

    async def propose_action(self, state: DiagnosisState) -> ModelInvocation:
        """将最小上下文转换为消息，并检验模型的候选动作。"""
        model_context = state.get("model_context")
        if model_context is None:
            raise ValueError("model_context must be built before proposing an action")

        payload = {
            "user_query": state["user_query"],
            # 只传递 Context Manager 已筛选的条目，隔离运行时内部状态。
            "context": [item.model_dump(mode="json") for item in model_context.items],
            "truncated": model_context.truncated,
            "plan_version": state["plan_version"],
            "replan_requested": state.get("replan_requested", False),
            "replan_reason": state.get("replan_reason"),
            "replan_feedback": state.get("replan_feedback"),
            "replan_correction_count": state.get("replan_correction_count", 0),
            "replan_count": state.get("replan_count", 0),
            "question_count": state.get("question_count", 0),
            # 将 Harness 计算出的可执行计划项显式传给模型，避免其误用已完成计划项。
            "runnable_plan_item_ids": self._runnable_plan_item_ids(state),
            "evidence_ids": [str(evidence.evidence_id) for evidence in state["evidence"]],
            "available_tools": self._tools,
        }
        messages: list[BaseMessage] = [
            SystemMessage(content=self._SYSTEM_INSTRUCTION),
            HumanMessage(
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        ]

        response = await self._action_runnable.ainvoke(messages)
        if isinstance(response, dict) and "parsed" in response:
            # include_raw=True 时，LangChain 通常返回 raw、parsed 和 parsing_error。
            parsing_error = response.get("parsing_error")
            parsed_action = response.get("parsed")
            if parsing_error is not None or parsed_action is None:
                recovered_action = self._recover_final_action(response.get("raw"))
                if recovered_action is not None:
                    return ModelInvocation(
                        action=recovered_action,
                        usage=self._extract_usage(response.get("raw")),
                    )
                if isinstance(parsing_error, BaseException):
                    raise parsing_error
                if parsing_error is not None:
                    raise ValueError(f"failed to parse structured action: {parsing_error}")
                raise ValueError("structured action response did not contain a parsed action")

            action = (
                parsed_action
                if isinstance(parsed_action, AgentAction)
                else AgentAction.model_validate(parsed_action)
            )
            return ModelInvocation(
                action=action,
                usage=self._extract_usage(response.get("raw")),
            )

        if isinstance(response, AgentAction):
            return ModelInvocation(action=response, usage=ModelUsage())

        # 兼容旧的字典结构化响应；此类响应不提供供应商用量。
        return ModelInvocation(
            action=AgentAction.model_validate(response),
            usage=ModelUsage(),
        )

    @staticmethod
    def _runnable_plan_item_ids(state: DiagnosisState) -> list[str]:
        """返回依赖已满足且尚未执行的计划项，作为模型动作的硬边界。"""
        item_by_id = {item.id: item for item in state["plan"]}
        return [
            str(item.id)
            for item in state["plan"]
            if item.status is PlanStatus.PENDING
            and all(
                item_by_id[dependency_id].status is PlanStatus.COMPLETED
                for dependency_id in item.depends_on
            )
        ]

    @staticmethod
    def _recover_final_action(raw_response: Any) -> AgentAction | None:
        """忽略最终报告中无语义的计划编号，兼容部分模型的冗余字段。"""
        for args in LangChainActionProvider._raw_action_args(raw_response):
            if args.get("action_type") != "final_answer":
                continue
            sanitized_args = dict(args)
            sanitized_args.pop("plan_item_id", None)
            try:
                return AgentAction.model_validate(sanitized_args)
            except ValueError:
                continue
        return None

    @staticmethod
    def _raw_action_args(raw_response: Any) -> list[Mapping[str, Any]]:
        """从 LangChain 标准和兼容端点的函数调用字段中读取动作参数。"""
        candidates: list[Any] = []
        for field_name in ("tool_calls", "invalid_tool_calls"):
            tool_calls = getattr(raw_response, field_name, ())
            if isinstance(tool_calls, list):
                candidates.extend(
                    tool_call.get("args", tool_call.get("function"))
                    for tool_call in tool_calls
                    if isinstance(tool_call, Mapping)
                )

        additional_kwargs = getattr(raw_response, "additional_kwargs", {})
        if isinstance(additional_kwargs, Mapping):
            raw_tool_calls = additional_kwargs.get("tool_calls", ())
            if isinstance(raw_tool_calls, list):
                candidates.extend(
                    tool_call.get("function", tool_call)
                    for tool_call in raw_tool_calls
                    if isinstance(tool_call, Mapping)
                )
        candidates.append(getattr(raw_response, "content", None))

        action_args: list[Mapping[str, Any]] = []
        for candidate in candidates:
            if isinstance(candidate, Mapping):
                candidate = candidate.get("arguments", candidate)
            if isinstance(candidate, str):
                try:
                    candidate = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            if isinstance(candidate, Mapping):
                action_args.append(candidate)
        return action_args

    @staticmethod
    def _as_non_negative_int(value: Any) -> int:
        """将供应商返回的 Token 数量转换为非负整数。"""
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _extract_usage(self, raw_response: Any) -> ModelUsage:
        """兼容 LangChain 常见 usage_metadata 和 response_metadata 结构。"""
        usage_metadata = getattr(raw_response, "usage_metadata", None)
        if not isinstance(usage_metadata, dict):
            usage_metadata = {}

        response_metadata = getattr(raw_response, "response_metadata", None)
        if not isinstance(response_metadata, dict):
            response_metadata = {}

        token_usage = response_metadata.get("token_usage") or response_metadata.get("usage", {})
        if not isinstance(token_usage, dict):
            token_usage = {}

        input_tokens = self._as_non_negative_int(
            usage_metadata.get(
                "input_tokens",
                token_usage.get("prompt_tokens", token_usage.get("input_tokens", 0)),
            )
        )
        output_tokens = self._as_non_negative_int(
            usage_metadata.get(
                "output_tokens",
                token_usage.get(
                    "completion_tokens",
                    token_usage.get("output_tokens", 0),
                ),
            )
        )

        estimated_cost_usd = (
            input_tokens / 1_000 * self._input_cost_per_1k_tokens
            + output_tokens / 1_000 * self._output_cost_per_1k_tokens
        )
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
        )
