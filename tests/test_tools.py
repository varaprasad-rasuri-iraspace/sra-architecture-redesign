"""Authorization, validation, and tenant isolation for write tools."""

import pytest
from pydantic import ValidationError

from sra.tools.base import ActionDenied, TenantContext
from sra.tools.remediations import FlushCacheTool, RestartServiceTool, ToolRegistry


class _RestartStub:
    def restart(self, tenant_id: str, service_name: str, environment: str) -> dict:
        return {"job_id": "rst-1", "tenant_id": tenant_id, "service_name": service_name, "environment": environment}


class _CacheStub:
    def flush(self, tenant_id: str, cache_namespace: str) -> dict:
        return {"flushed": True, "tenant_id": tenant_id, "cache_namespace": cache_namespace}


def _ctx(**overrides) -> TenantContext:
    data = {
        "tenant_id": "acme",
        "session_token": "session-token-16c",
        "principal": "agent.sra",
        "role": "sre",
        "scopes": frozenset({"remediation:restart"}),
    }
    data.update(overrides)
    return TenantContext(**data)


def test_restart_requires_scope():
    tool = RestartServiceTool(_RestartStub())
    with pytest.raises(ActionDenied, match="required scopes"):
        tool.invoke(
            _ctx(scopes=frozenset()),
            {
                "tenant_id": "acme",
                "service_name": "sra-api",
                "environment": "staging",
                "reason": "Incident: elevated 5xx",
            },
        )


def test_restart_rejects_cross_tenant_target():
    tool = RestartServiceTool(_RestartStub())
    with pytest.raises(ActionDenied, match="Cross-tenant"):
        tool.invoke(
            _ctx(),
            {
                "tenant_id": "globex",
                "service_name": "sra-api",
                "environment": "staging",
                "reason": "Incident: elevated 5xx",
            },
        )


def test_restart_rejects_unknown_service():
    tool = RestartServiceTool(_RestartStub())
    with pytest.raises(ValidationError):
        tool.invoke(
            _ctx(),
            {
                "tenant_id": "acme",
                "service_name": "payroll-db",
                "environment": "prod",
                "reason": "Incident: elevated 5xx",
            },
        )


def test_restart_dry_run_does_not_call_adapter():
    class _FailAdapter:
        def restart(self, *args, **kwargs):
            raise AssertionError("adapter should not be called during dry-run")

    tool = RestartServiceTool(_FailAdapter())
    result = tool.invoke(
        _ctx(),
        {
            "tenant_id": "acme",
            "service_name": "sra-worker",
            "environment": "prod",
            "reason": "Incident: worker heartbeat lost",
        },
        dry_run=True,
    )
    assert result.ok is True
    assert result.dry_run is True


def test_flush_cache_happy_path_and_registry_spec():
    tool = FlushCacheTool(_CacheStub())
    context = _ctx(scopes=frozenset({"remediation:cache"}))
    result = tool.invoke(
        context,
        {
            "tenant_id": "acme",
            "cache_namespace": "retrieval-v14",
            "reason": "Stale embeddings after reindex",
        },
    )
    assert result.ok is True
    assert result.payload["flushed"] is True
    assert "session-token-16c" not in result.model_dump_json()

    registry = ToolRegistry([tool])
    specs = registry.list_specs()
    assert specs[0]["name"] == "flush_cache"
    assert "remediation:cache" in specs[0]["required_scopes"]
