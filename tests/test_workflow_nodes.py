"""Offline tests for LangGraph workflow node behavior."""

from __future__ import annotations

import unittest

from agent4design.rhapsody.verifier import VerificationReport
from agent4design.services.agent_service import (
    AgentSyncPlanResult,
    AgentSyncRequest,
    AgentSyncResult,
    ExecuteSyncResult,
)
from agent4design.services.model_sync import ModelSyncRequest, ModelSyncResult
from agent4design.services.sync_plan import ModelSyncPlan
from agent4design.workflows.nodes import SyncWorkflowNodes


class FakeWorkflowService:
    def __init__(self) -> None:
        self.plan = AgentSyncPlanResult(
            success=True,
            requires_approval=True,
            model=ModelSyncPlan(
                success=True,
                requires_approval=True,
                items=[],
            ),
            activities=[],
        )
        self.execute_requests = []
        self.save_requests = []

    def execute_sync(self, request):
        self.execute_requests.append(request)
        return ExecuteSyncResult(
            success=True,
            plan=self.plan,
            sync=AgentSyncResult(
                success=True,
                model=ModelSyncResult(success=True),
                activities=[],
            ),
            verification=VerificationReport(success=True),
            saved=False,
        )

    def save_project_approved(self, request):
        self.save_requests.append(request)
        return {"saved": True}


def _state_request(*, save_project: bool = True):
    return AgentSyncRequest(
        model=ModelSyncRequest(save_project=save_project),
        activities=[],
    ).model_dump(mode="json")


class WorkflowNodeTests(unittest.TestCase):
    def test_execute_sync_uses_human_approval_and_defers_save(self) -> None:
        service = FakeWorkflowService()
        nodes = SyncWorkflowNodes(service, lambda payload: {"approved": True})
        state = {
            "request": _state_request(save_project=True),
            "plan": service.plan.model_dump(mode="json"),
        }

        state.update(nodes.request_write_approval(state))
        update = nodes.execute_sync(state)

        self.assertTrue(state["write_approved"])
        self.assertTrue(update["success"])
        self.assertIn("execution", update)
        self.assertIn("verification", update)
        self.assertEqual(len(service.execute_requests), 1)
        self.assertTrue(service.execute_requests[0].approved)
        self.assertFalse(service.execute_requests[0].request.model.save_project)

    def test_denied_write_approval_stops_workflow(self) -> None:
        service = FakeWorkflowService()
        nodes = SyncWorkflowNodes(service, lambda payload: {"approved": False})
        state = {
            "request": _state_request(),
            "plan": service.plan.model_dump(mode="json"),
        }

        update = nodes.request_write_approval(state)

        self.assertFalse(update["write_approved"])
        self.assertIn("Rhapsody model changes were not approved.", update["errors"])

    def test_save_project_uses_approved_service_method(self) -> None:
        service = FakeWorkflowService()
        nodes = SyncWorkflowNodes(service, lambda payload: {"approved": True})

        update = nodes.save_project({"request": _state_request()})

        self.assertTrue(update["saved"])
        self.assertEqual(len(service.save_requests), 1)
        self.assertTrue(service.save_requests[0].approved)


if __name__ == "__main__":
    unittest.main()
