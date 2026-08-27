"""FastAPI 路由使用的显式应用依赖。"""

from typing import cast

from fastapi import Request

from app.tools.scenarios import ScenarioStore


def get_scenario_store(request: Request) -> ScenarioStore:
    """从应用状态读取由应用工厂注入的场景目录。"""
    # 应用工厂始终初始化该字段；cast 仅帮助静态类型检查。
    return cast(ScenarioStore, request.app.state.scenario_store)
