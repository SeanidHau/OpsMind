"""诊断计划的结构校验与版本化修订。"""

from __future__ import annotations

from uuid import UUID

from app.models.contracts import ActionType, AgentAction, PlanItem, PlanRevision, PlanStatus


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
    def start_item(
        cls,
        items: list[PlanItem],
        item_id: UUID,
        *,
        previous_tool_attempts: tuple[AgentAction, ...] = (),
    ) -> list[PlanItem]:
        """校验依赖后，将待执行计划项转为 in_progress。"""
        cls.ensure_tool_attempt_capacity(items, item_id, previous_tool_attempts)
        return cls._transition_item(items, item_id, PlanStatus.IN_PROGRESS)

    @classmethod
    def ensure_tool_attempt_capacity(
        cls,
        items: list[PlanItem],
        item_id: UUID,
        previous_tool_attempts: tuple[AgentAction, ...],
    ) -> None:
        """检查绑定计划项是否仍可开始下一次真实工具尝试。"""
        cls._validate_items(items)
        item = next((current for current in items if current.id == item_id), None)
        if item is None:
            raise ValueError("action references an unknown plan item")
        if item.max_tool_attempts is None:
            return

        attempt_count = sum(
            1
            for action in previous_tool_attempts
            if action.action_type is ActionType.CALL_TOOL and action.plan_item_id == item_id
        )
        if attempt_count >= item.max_tool_attempts:
            raise ValueError("plan item tool attempt limit is reached")

    @classmethod
    def complete_item(cls, items: list[PlanItem], item_id: UUID) -> list[PlanItem]:
        """将已开始的计划项转为 completed。"""
        return cls._transition_item(items, item_id, PlanStatus.COMPLETED)

    @classmethod
    def _transition_item(
        cls,
        items: list[PlanItem],
        item_id: UUID,
        target_status: PlanStatus,
    ) -> list[PlanItem]:
        """返回状态迁移后的计划副本，绝不原地修改 LangGraph 状态。"""
        cls._validate_items(items)
        item_by_id = {item.id: item for item in items}
        item = item_by_id.get(item_id)
        if item is None:
            raise ValueError("action references an unknown plan item")

        if target_status is PlanStatus.IN_PROGRESS:
            incomplete_dependencies = [
                dependency_id
                for dependency_id in item.depends_on
                if item_by_id[dependency_id].status is not PlanStatus.COMPLETED
            ]
            if incomplete_dependencies:
                raise ValueError("plan item dependencies are not completed")
            if item.status not in (PlanStatus.PENDING, PlanStatus.IN_PROGRESS):
                raise ValueError("plan item cannot be started from its current status")

        if target_status is PlanStatus.COMPLETED and item.status is not PlanStatus.IN_PROGRESS:
            raise ValueError("plan item must be in progress before completion")

        return [
            current.model_copy(update={"status": target_status})
            if current.id == item_id
            else current.model_copy(deep=True)
            for current in items
        ]

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
