"""诊断计划的结构校验与版本化修订。"""

from __future__ import annotations

from uuid import UUID

from app.models.contracts import PlanItem, PlanRevision


class PlanManager:
    """校验模型提交的计划，并生成可审计的计划修订版本。"""

    def create_revision(
        self,
        *,
        items: list[PlanItem],
        version: int,
        reason: str,
    ) -> PlanRevision:
        """校验完整计划后，创建一个不可变的版本化快照。"""
        self._validate_items(items)

        return PlanRevision(
            version=version,
            reason=reason,
            items=[item.model_copy(deep=True) for item in items],
        )

    @classmethod
    def _validate_items(cls, items: list[PlanItem]) -> None:
        """拒绝重复 ID、未知依赖、自依赖和循环依赖。"""
        item_ids = [item.id for item in items]
        known_ids = set(item_ids)

        if len(known_ids) != len(item_ids):
            raise ValueError("plan items must not contain duplicate IDs")

        for item in items:
            if item.id in item.depends_on:
                raise ValueError("plan item must not depend on itself")

            unknown_dependencies = set(item.depends_on) - known_ids
            if unknown_dependencies:
                raise ValueError("plan item depends on an unknown item")

        # 深度优先遍历检测依赖环，避免计划永远无法满足前置条件。
        dependencies = {item.id: item.depends_on for item in items}
        visiting: set[UUID] = set()
        visited: set[UUID] = set()

        def visit(item_id: UUID) -> None:
            if item_id in visiting:
                raise ValueError("plan items must not contain dependency cycles")
            if item_id in visited:
                return

            visiting.add(item_id)
            for dependency_id in dependencies[item_id]:
                visit(dependency_id)
            visiting.remove(item_id)
            visited.add(item_id)

        for item_id in dependencies:
            visit(item_id)
