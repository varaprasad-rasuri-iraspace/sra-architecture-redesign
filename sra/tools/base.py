"""
Standard MCP / Action Plugin Interface.

Every write tool is a typed plugin: Pydantic-validated input, explicit required
scopes, tenant + session token isolation, and a structured audit result.
No tool may execute unless the caller presents a bound tenant context.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("sra.tools")

TInput = TypeVar("TInput", bound=BaseModel)


class ActionDenied(Exception):
    """Raised when a tool invocation fails authorization or tenant isolation."""


class TenantContext(BaseModel):
    """Caller identity bound to a single tenant and short-lived session token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(..., min_length=1)
    session_token: str = Field(..., min_length=16)
    principal: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    scopes: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("tenant_id", "principal", "role")
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be a non-empty string")
        return cleaned

    def token_fingerprint(self) -> str:
        """Return a non-reversible session token fingerprint for audit logs."""
        return hashlib.sha256(self.session_token.encode("utf-8")).hexdigest()[:16]


class ActionResult(BaseModel):
    """Canonical tool response returned to the agent loop and audit trail."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    tool_name: str
    tenant_id: str
    dry_run: bool = False
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    audit_id: str
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActionTool(ABC, Generic[TInput]):
    """
    MCP-style action plugin contract.

    Implementations declare a stable name, least-privilege scopes, and a
    Pydantic input schema. The base class enforces tenant isolation and
    authorization before `execute` is called.
    """

    name: str
    description: str
    required_scopes: frozenset[str]
    input_model: type[TInput]

    def invoke(
        self,
        context: TenantContext,
        raw_input: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> ActionResult:
        self._authorize(context)
        payload = self.input_model.model_validate(raw_input)
        self._assert_tenant_isolation(context, payload)

        logger.info(
            "Invoking tool=%s tenant=%s principal=%s dry_run=%s token=%s",
            self.name,
            context.tenant_id,
            context.principal,
            dry_run,
            context.token_fingerprint(),
        )
        return self.execute(context, payload, dry_run=dry_run)

    def _authorize(self, context: TenantContext) -> None:
        if not context.session_token:
            raise ActionDenied("Missing session token; refusing unauthenticated tool call.")
        missing = self.required_scopes - context.scopes
        if missing:
            raise ActionDenied(
                f"Principal '{context.principal}' lacks required scopes: {sorted(missing)}"
            )

    def _assert_tenant_isolation(self, context: TenantContext, payload: TInput) -> None:
        target_tenant = getattr(payload, "tenant_id", None)
        if target_tenant and target_tenant != context.tenant_id:
            raise ActionDenied(
                f"Cross-tenant invocation denied: context={context.tenant_id} target={target_tenant}"
            )

    @abstractmethod
    def execute(
        self,
        context: TenantContext,
        payload: TInput,
        *,
        dry_run: bool,
    ) -> ActionResult:
        """Perform the privileged side effect (or a dry-run preview)."""
