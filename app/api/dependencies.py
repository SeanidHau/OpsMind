"""FastAPI 路由使用的显式应用依赖。"""

from typing import cast

from fastapi import Request

from app.tools.registry import ToolRegistry
from app.tools.scenarios import ScenarioStore


def get_scenario_store(request: Request) -> ScenarioStore:
    """从应用状态读取由应用工厂注入的场景目录。"""
    # 应用工厂始终初始化该字段；cast 仅帮助静态类型检查。
    return cast(ScenarioStore, request.app.state.scenario_store)


def get_tool_registry(request: Request) -> ToolRegistry:
    """从应用状态读取与场景存储绑定的只读工具注册表。"""
    # 诊断运行路由将通过该依赖取得可执行工具，不自行创建注册表。
    return cast(ToolRegistry, request.app.state.tool_registry)
