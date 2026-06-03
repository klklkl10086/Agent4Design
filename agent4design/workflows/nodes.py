"""Node implementations for the optional LangGraph synchronization workflow."""

from __future__ import annotations

from typing import Any, Callable, Dict

from agent4design.services.agent_service import (
    Agent4DesignService,
    AgentSyncRequest,
    ApprovalRequest,
    EmptyRequest,
    ExecuteSyncRequest,
    InitializeRequest,
)
from agent4design.workflows.state import SyncWorkflowState


InterruptFn = Callable[[Dict[str, Any]], Any]


def _approved(decision: Any) -> bool:
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, dict):
        return bool(decision.get("approved", False))
    return False


def _append_error(state: SyncWorkflowState, error: str) -> Dict[str, Any]:
    return {"errors": [*state.get("errors", []), error], "success": False}


class SyncWorkflowNodes:
    """Create resumable workflow nodes around the framework-neutral service."""

    def __init__(
        self,
        service: Agent4DesignService,
        interrupt_fn: InterruptFn,
    ) -> None:
        self.service = service
        self.interrupt_fn = interrupt_fn

    @staticmethod
    def _request(state: SyncWorkflowState) -> AgentSyncRequest:
        return AgentSyncRequest.model_validate(state["request"])

    def initialize(self, state: SyncWorkflowState) -> Dict[str, Any]:
        try:
            context = self.service.initialize(
                InitializeRequest(
                    select_current_target=state.get("select_current_target", True)
                )
            )
            return {"context": context, "errors": []}
        except Exception as exc:
            return _append_error(state, str(exc))

    def refresh_types(self, state: SyncWorkflowState) -> Dict[str, Any]:
        try:
            result = self.service.refresh_type_registry(EmptyRequest())
            return {"type_registry": result}
        except Exception as exc:
            return _append_error(state, str(exc))

    def plan_sync(self, state: SyncWorkflowState) -> Dict[str, Any]:
        try:
            plan = self.service.plan_sync(self._request(state))
            updates: Dict[str, Any] = {"plan": plan.model_dump(mode="json")}
            if not plan.success:
                updates.update(
                    _append_error(state, "Synchronization plan contains rejected items.")
                )
            return updates
        except Exception as exc:
            return _append_error(state, str(exc))

    def request_write_approval(self, state: SyncWorkflowState) -> Dict[str, Any]:
        plan = state.get("plan", {})
        if not plan.get("requires_approval", False):
            return {"write_approved": True}
        decision = self.interrupt_fn(
            {
                "type": "rhapsody_write_approval",
                "message": "Approve changes to the active Rhapsody model.",
                "plan": plan,
            }
        )
        approved = _approved(decision)
        if not approved:
            return {
                **_append_error(state, "Rhapsody model changes were not approved."),
                "write_approved": False,
            }
        return {"write_approved": True}

    def execute_sync(self, state: SyncWorkflowState) -> Dict[str, Any]:
        try:
            request = self._request(state)
            model_request = request.model.model_copy(update={"save_project": False})
            sync_request = request.model_copy(update={"model": model_request})
            result = self.service.execute_sync(
                ExecuteSyncRequest(
                    request=sync_request,
                    approved=state.get("write_approved", False),
                    verify_after_sync=True,
                )
            )
            updates: Dict[str, Any] = {
                "execution": result.model_dump(mode="json"),
                "model_sync": result.sync.model.model_dump(mode="json"),
                "activities": [
                    item.model_dump(mode="json")
                    for item in result.sync.activities
                ],
                "saved": result.saved,
                "success": result.success and not state.get("errors", []),
            }
            if result.verification is not None:
                updates["verification"] = result.verification.model_dump(mode="json")
            if not result.success:
                updates.update(_append_error(state, "Agent4Design synchronization failed."))
            return updates
        except Exception as exc:
            return _append_error(state, str(exc))

    def request_save_approval(self, state: SyncWorkflowState) -> Dict[str, Any]:
        request = self._request(state)
        if not request.model.save_project:
            return {"save_approved": False}
        decision = self.interrupt_fn(
            {
                "type": "rhapsody_save_approval",
                "message": "Verification passed. Approve saving the active Rhapsody project.",
                "verification": state.get("verification", {}),
            }
        )
        approved = _approved(decision)
        if not approved:
            return {
                **_append_error(state, "Saving the Rhapsody project was not approved."),
                "save_approved": False,
            }
        return {"save_approved": True}

    def save_project(self, state: SyncWorkflowState) -> Dict[str, Any]:
        try:
            self.service.save_project_approved(ApprovalRequest(approved=True))
            return {"saved": True}
        except Exception as exc:
            return _append_error(state, str(exc))

    @staticmethod
    def summarize(state: SyncWorkflowState) -> Dict[str, Any]:
        return {
            "success": not state.get("errors", []),
            "saved": state.get("saved", False),
        }
