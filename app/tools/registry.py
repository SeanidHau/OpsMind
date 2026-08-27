"""工具注册、参数检验和异步执行入口。"""

from __future__ import annotations

from typing import Any, Protocol

from app.models.contracts import (
    ActionType,
    AgentAction,
    ToolDefinition,
    ToolPolicy,
)


class ToolExecutionError(RuntimeError):
    """工具未注册或调用参数不符合定义时抛出。"""


class ToolHandler(Protocol):
    """已注册工具的异步处理函数协议。"""

    async def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        """接受经过校验的参数并返回结构化观察结果。"""


class ToolRegistry:
    """维护工具定义与处理函数，并作为 Harness 的 ToolExecutor。"""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, ToolHandler]] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        """注册工具；同名工具会导致不确定执行，因此直接拒绝。"""
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")

        self._tools[definition.name] = (definition, handler)

    def policies(self) -> tuple[ToolPolicy, ...]:
        """将注册定义投影为 ActionPolicy 所需的风险策略。"""
        return tuple(
            ToolPolicy(
                name=definition.name,
                risk_level=definition.risk_level,
                read_only=definition.read_only,
                requires_approval=definition.requires_approval,
                required_args=definition.required_args,
                allowed_args=definition.allowed_args,
                # 将工具定义中的单工具预算投影到策略层。
                max_calls_per_run=definition.max_calls_per_run,
            )
            for definition, _ in self._tools.values()
        )

    async def execute(self, action: AgentAction) -> dict[str, Any]:
        """校验动作和参数后执行对应处理函数。"""
        if action.action_type is not ActionType.CALL_TOOL:
            raise ToolExecutionError("registry only executes call_tool actions")

        tool_name = action.tool_name
        if tool_name is None:
            raise ToolExecutionError("call_tool action requires tool_name")

        registered_tool = self._tools.get(tool_name)
        if registered_tool is None:
            raise ToolExecutionError(f"tool is not registered: {tool_name}")

        definition, handler = registered_tool
        self._validate_args(definition, action.tool_args)

        # 传入副本，避免处理函数修改 ActionAgent 中保存的原始数据
        return await handler(dict(action.tool_args))

    @staticmethod
    def _validate_args(definition: ToolDefinition, tool_args: dict[str, Any]) -> None:
        """在进入处理函数前拦截缺失和未声明的参数。"""
        argument_names = set(tool_args)

        missing_args = sorted(set(definition.required_args) - argument_names)
        if missing_args:
            names = ", ".join(missing_args)
            raise ToolExecutionError(f"missing required args: {names}")

        unexpected_args = sorted(argument_names - set(definition.allowed_args))
        if unexpected_args:
            names = ", ".join(unexpected_args)
            raise ToolExecutionError(f"unexpected args: {names}")
