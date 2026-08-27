"""可复现场景目录的只读 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_scenario_store
from app.api.schemas.scenarios import ScenarioSummary
from app.tools.scenarios import ScenarioStore

router = APIRouter(prefix="/api/v1", tags=["scenarios"])


@router.get(
    "/scenarios",
    response_model=list[ScenarioSummary],
    status_code=status.HTTP_200_OK,
    summary="列出可选择的故障诊断场景",
)
async def get_scenarios(
    scenario_store: Annotated[ScenarioStore, Depends(get_scenario_store)],
) -> list[ScenarioSummary]:
    """返回稳定排序的场景摘要，不泄露原始诊断证据。"""
    return [
        ScenarioSummary(
            scenario_id=scenario.scenario_id,
            service=scenario.service,
            log_count=len(scenario.logs),
            metric_names=sorted(scenario.metrics),
            dependency_count=len(scenario.dependencies),
        )
        for scenario in scenario_store.list_scenarios()
    ]
