"""Security-bound MCP-style action plugins for SRA remediations."""

from sra.tools.base import ActionDenied, ActionResult, ActionTool, TenantContext
from sra.tools.remediations import (
    FlushCacheInput,
    FlushCacheTool,
    PatchConfigInput,
    PatchConfigTool,
    RestartServiceInput,
    RestartServiceTool,
    ToolRegistry,
)

__all__ = [
    "ActionDenied",
    "ActionResult",
    "ActionTool",
    "FlushCacheInput",
    "FlushCacheTool",
    "PatchConfigInput",
    "PatchConfigTool",
    "RestartServiceInput",
    "RestartServiceTool",
    "TenantContext",
    "ToolRegistry",
]
