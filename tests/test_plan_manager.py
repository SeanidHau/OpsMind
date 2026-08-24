"""计划结构校验与版本化修订的验收测试。"""

from uuid import UUID, uuid4

import pytest

from app.harness.plan import PlanManager
from app.models.contracts import PlanItem


def plan_item(
    title: str,
    *,
    item_id: UUID | None = None,
    depends_on: list[UUID] | None = None,
) -> PlanItem:
    """构造可按依赖关系调整的测试计划项。"""
    return PlanItem(
        id=item_id or uuid4(),
        title=title,
        rationale="验证当前诊断路径。",
        depends_on=depends_on or [],
    )


def test_manager_creates_isolated_versioned_revision() -> None:
    """合法依赖计划应生成深拷贝的版本化快照。"""
    first = plan_item("查询接口延迟")
    second = plan_item("查询数据库连接池", depends_on=[first.id])

    revision = PlanManager().create_revision(
        items=[first, second],
        version=2,
        reason="指标结果需要补充数据库证据。",
    )
    first.title = "外部调用方修改"

    assert revision.version == 2
    assert revision.items[1].depends_on == [first.id]
    assert revision.items[0].title == "查询接口延迟"


@pytest.mark.parametrize(
    ("items", "error"),
    [
        (
            lambda: (lambda item: [item, item.model_copy(deep=True)])(plan_item("重复 ID")),
            "duplicate IDs",
        ),
        (
            lambda: [plan_item("未知依赖", depends_on=[uuid4()])],
            "unknown item",
        ),
        (
            lambda: (lambda item: [item.model_copy(update={"depends_on": [item.id]})])(
                plan_item("自依赖")
            ),
            "depend on itself",
        ),
        (
            lambda: (
                lambda first_id, second_id: [
                    plan_item("第一项", item_id=first_id, depends_on=[second_id]),
                    plan_item("第二项", item_id=second_id, depends_on=[first_id]),
                ]
            )(uuid4(), uuid4()),
            "dependency cycles",
        ),
    ],
)
def test_manager_rejects_invalid_dependency_graph(
    items: object,
    error: str,
) -> None:
    """重复、未知、自引用和循环依赖都不能进入 Harness 状态。"""
    with pytest.raises(ValueError, match=error):
        PlanManager().create_revision(
            items=items(),  # type: ignore[operator]
            version=1,
            reason="测试无效计划。",
        )
