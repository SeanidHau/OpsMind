"""基于 LangChain 结构化输出的动作提供器。"""

from __future__ import annotations

import json
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.models.contracts import AgentAction, DiagnosisState


class ActionRunnable(Protocol):
    """能够异步返回结构化动作的 LangChain Runnable。"""

    async def ainvoke(self, input: list[BaseMessage]) -> AgentAction | dict[str, Any]:
        """接收消息列表并返回模型结构化输出。"""


class StructuredActionChatModel(Protocol):
    """支持 Pydantic Structured Output 的 LangChain 聊天模型。"""

    def with_structured_output(self, schema: type[AgentAction]) -> ActionRunnable:
        """将聊天模型绑定到指定的结构化输出 schema。"""


class LangChainActionProvider:
    """仅基于最小模型上下文，让 LangChain 模型提出下一步动作。"""

    _SYSTEM_INSTRUCTION = """\
    你是 OpsMind 的诊断规划模型。只能提出符合 AgentAction schema 的下一步动作。
    不要声称执行过工具；工具执行由 Harness 完成。
    证据不足时，选择 call_tool 或 ask_user。

    只有证据足够时才选择 final_answer。final_answer 必须携带 report。
    report 的摘要、候选根因和建议必须基于当前 context；
    report.evidence_ids 只能引用 context 中形如 evidence:<evidence_id> 的条目，
    并且写入时应去掉 evidence: 前缀。
    不要引用未出现在 context 中的证据 ID。
    """

    def __init__(self, chat_model: StructuredActionChatModel) -> None:
        """绑定 `AgentAction` schema，限制模型输出形状。"""
        self._action_runnable = chat_model.with_structured_output(AgentAction)

    async def propose_action(self, state: DiagnosisState) -> AgentAction:
        """将最小上下文转换为消息，并检验模型的候选动作。"""
        model_context = state.get("model_context")
        if model_context is None:
            raise ValueError("model_context must be built before proposing an action")

        payload = {
            "user_query": state["user_query"],
            # 只传递 Context Manager 已筛选的条目，隔离运行时内部状态。
            "context": [item.model_dump(mode="json") for item in model_context.items],
            "truncated": model_context.truncated,
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
        if isinstance(response, AgentAction):
            return response

        # 少数模型适配器返回字典时，仍由 Pydantic 执行动作契约校验。
        return AgentAction.model_validate(response)
