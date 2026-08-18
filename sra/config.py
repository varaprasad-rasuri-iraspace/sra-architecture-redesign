"""
System configuration and cost management parameters for SRA.
Enforces per-ticket budgets, max iteration caps, and model routing parameters.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelTier:
    name: str
    input_cost_per_1k: float
    output_cost_per_1k: float
    max_tokens: int


class Config:
    # Model Tier Definitions
    FAST_MODEL = ModelTier(
        name="gpt-4o-mini",
        input_cost_per_1k=0.00015,
        output_cost_per_1k=0.0006,
        max_tokens=2048,
    )
    REASONING_MODEL = ModelTier(
        name="claude-3-5-sonnet-20241022",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        max_tokens=4096,
    )

    # Agent Loop & Budget Boundaries
    MAX_AGENT_LOOPS: int = 5
    PER_TICKET_COST_CEILING_USD: float = 0.50  # Hard budget ceiling per ticket
    TARGET_GROUNDEDNESS_THRESHOLD: float = 0.88

    # Tenant Isolation & Tracing
    OTEL_EXPORTER_OTLP_ENDPOINT: str = os.getenv("OTEL_ENDPOINT", "http://localhost:4317")
    VECTOR_DB_COLLECTION: str = "sra_knowledge_v14"

    # Ingestion resilience (post-v14 silent-failure fix)
    INGESTION_MAX_RETRIES: int = 5
    INGESTION_BACKOFF_BASE_SECONDS: float = 0.5
    INGESTION_HEARTBEAT_INTERVAL_SECONDS: int = 30
