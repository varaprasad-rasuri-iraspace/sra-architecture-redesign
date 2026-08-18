"""
Security-bound write action tools (MCP pattern).

These plugins move SRA from read-only Q&A to scoped remediations. Every call is
Pydantic-validated, tenant-bound, and least-privilege scoped. Side effects are
delegated to injected adapters so the policy layer stays unit-testable.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sra.tools.base import ActionResult, ActionTool, TenantContext

logger = logging.getLogger("sra.tools.remediations")

ALLOWED_ENVIRONMENTS = frozenset({"staging", "prod"})
ALLOWED_SERVICES = frozenset({"sra-api", "sra-worker", "sra-ingest", "cache-gateway"})
ALLOWED_CONFIG_KEYS = frozenset(
    {
        "feature.auto_restart",
        "feature.cache_ttl_seconds",
        "feature.maintenance_mode",
    }
)


class RestartAdapter(Protocol):
    def restart(self, tenant_id: str, service_name: str, environment: str) -> dict[str, Any]: ...


class CacheAdapter(Protocol):
    def flush(self, tenant_id: str, cache_namespace: str) -> dict[str, Any]: ...


class ConfigAdapter(Protocol):
    def patch(self, tenant_id: str, key: str, value: str) -> dict[str, Any]: ...


class RestartServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=1)
    service_name: str
    environment: Literal["staging", "prod"]
    reason: str = Field(..., min_length=8, max_length=500)

    @field_validator("service_name")
    @classmethod
    def _known_service(cls, value: str) -> str:
        if value not in ALLOWED_SERVICES:
            raise ValueError(f"service_name must be one of {sorted(ALLOWED_SERVICES)}")
        return value


class FlushCacheInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=1)
    cache_namespace: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=8, max_length=500)


class PatchConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(..., min_length=1)
    key: str
    value: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=8, max_length=500)

    @field_validator("key")
    @classmethod
    def _allowlisted_key(cls, value: str) -> str:
        if value not in ALLOWED_CONFIG_KEYS:
            raise ValueError(f"key must be one of {sorted(ALLOWED_CONFIG_KEYS)}")
        return value


def _audit_id() -> str:
    return uuid.uuid4().hex


class RestartServiceTool(ActionTool[RestartServiceInput]):
    name = "restart_service"
    description = "Restart a tenant-scoped SRA runtime service after a confirmed incident."
    required_scopes = frozenset({"remediation:restart"})
    input_model = RestartServiceInput

    def __init__(self, adapter: RestartAdapter):
        self._adapter = adapter

    def execute(
        self,
        context: TenantContext,
        payload: RestartServiceInput,
        *,
        dry_run: bool,
    ) -> ActionResult:
        preview = {
            "service_name": payload.service_name,
            "environment": payload.environment,
            "reason": payload.reason,
        }
        if dry_run:
            return ActionResult(
                ok=True,
                tool_name=self.name,
                tenant_id=context.tenant_id,
                dry_run=True,
                message="Dry-run: service restart would be issued.",
                payload=preview,
                audit_id=_audit_id(),
            )

        result = self._adapter.restart(
            tenant_id=context.tenant_id,
            service_name=payload.service_name,
            environment=payload.environment,
        )
        logger.info(
            "Restart issued tenant=%s service=%s env=%s audit_reason=%s",
            context.tenant_id,
            payload.service_name,
            payload.environment,
            payload.reason,
        )
        return ActionResult(
            ok=True,
            tool_name=self.name,
            tenant_id=context.tenant_id,
            message="Service restart accepted.",
            payload={**preview, **result},
            audit_id=_audit_id(),
        )


class FlushCacheTool(ActionTool[FlushCacheInput]):
    name = "flush_cache"
    description = "Flush a tenant-scoped cache namespace to clear stale retrieval state."
    required_scopes = frozenset({"remediation:cache"})
    input_model = FlushCacheInput

    def __init__(self, adapter: CacheAdapter):
        self._adapter = adapter

    def execute(
        self,
        context: TenantContext,
        payload: FlushCacheInput,
        *,
        dry_run: bool,
    ) -> ActionResult:
        preview = {"cache_namespace": payload.cache_namespace, "reason": payload.reason}
        if dry_run:
            return ActionResult(
                ok=True,
                tool_name=self.name,
                tenant_id=context.tenant_id,
                dry_run=True,
                message="Dry-run: cache flush would be issued.",
                payload=preview,
                audit_id=_audit_id(),
            )

        result = self._adapter.flush(
            tenant_id=context.tenant_id,
            cache_namespace=payload.cache_namespace,
        )
        return ActionResult(
            ok=True,
            tool_name=self.name,
            tenant_id=context.tenant_id,
            message="Cache namespace flushed.",
            payload={**preview, **result},
            audit_id=_audit_id(),
        )


class PatchConfigTool(ActionTool[PatchConfigInput]):
    name = "patch_config"
    description = "Apply an allowlisted tenant configuration patch."
    required_scopes = frozenset({"remediation:config"})
    input_model = PatchConfigInput

    def __init__(self, adapter: ConfigAdapter):
        self._adapter = adapter

    def execute(
        self,
        context: TenantContext,
        payload: PatchConfigInput,
        *,
        dry_run: bool,
    ) -> ActionResult:
        preview = {"key": payload.key, "value": payload.value, "reason": payload.reason}
        if dry_run:
            return ActionResult(
                ok=True,
                tool_name=self.name,
                tenant_id=context.tenant_id,
                dry_run=True,
                message="Dry-run: config patch would be applied.",
                payload=preview,
                audit_id=_audit_id(),
            )

        result = self._adapter.patch(
            tenant_id=context.tenant_id,
            key=payload.key,
            value=payload.value,
        )
        return ActionResult(
            ok=True,
            tool_name=self.name,
            tenant_id=context.tenant_id,
            message="Configuration patch applied.",
            payload={**preview, **result},
            audit_id=_audit_id(),
        )


class ToolRegistry:
    """Name-indexed registry used by the agent loop to dispatch MCP tool calls."""

    def __init__(self, tools: list[ActionTool[Any]]):
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> ActionTool[Any]:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown action tool: {name}") from exc

    def list_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "required_scopes": sorted(tool.required_scopes),
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]
