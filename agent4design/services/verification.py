"""Application service for read-only Rhapsody verification."""

from __future__ import annotations

from typing import List

from pydantic import Field

from agent4design.domain.models import FunctionSpec, MacroSpec, StrictModel, VariableSpec
from agent4design.rhapsody.verifier import (
    RhapsodyVerifier,
    VerificationReport,
    rhapsody_verifier,
)


class VerificationRequest(StrictModel):
    """Expected Rhapsody elements to check after synchronization."""

    macros: List[MacroSpec] = Field(default_factory=list)
    variables: List[VariableSpec] = Field(default_factory=list)
    functions: List[FunctionSpec] = Field(default_factory=list)
    activities: List[str] = Field(default_factory=list)


class VerificationService:
    """Expose the COM verifier as a framework-neutral use case."""

    def __init__(self, verifier: RhapsodyVerifier = rhapsody_verifier) -> None:
        self.verifier = verifier

    def verify(self, request: VerificationRequest) -> VerificationReport:
        """Run read-only checks against the active Rhapsody project."""
        return self.verifier.verify(
            macros=request.macros,
            variables=request.variables,
            functions=request.functions,
            activities=request.activities,
        )


verification_service = VerificationService()
