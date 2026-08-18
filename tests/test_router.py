"""Unit tests for cost controls and dynamic model routing."""

import pytest

from sra.config import Config
from sra.router import CostExceededException, ModelRouter


def test_routine_queries_use_fast_model():
    router = ModelRouter()
    model = router.select_model("how_to", historical_loop_count=0)
    assert model is Config.FAST_MODEL
    assert model.name == "gpt-4o-mini"


@pytest.mark.parametrize("category", ["error_troubleshooting", "action_remediation"])
def test_complex_categories_escalate_to_reasoning_model(category: str):
    router = ModelRouter()
    model = router.select_model(category, historical_loop_count=0)
    assert model is Config.REASONING_MODEL


def test_repeated_loops_escalate_even_for_routine_categories():
    router = ModelRouter()
    model = router.select_model("how_to", historical_loop_count=2)
    assert model is Config.REASONING_MODEL


def test_cost_audit_accumulates_step_cost():
    router = ModelRouter()
    total = router.calculate_and_audit_cost(
        model=Config.FAST_MODEL,
        prompt_tokens=1000,
        completion_tokens=1000,
        accumulated_cost=0.0,
    )
    expected = Config.FAST_MODEL.input_cost_per_1k + Config.FAST_MODEL.output_cost_per_1k
    assert total == pytest.approx(expected)


def test_cost_ceiling_terminates_execution():
    router = ModelRouter()
    with pytest.raises(CostExceededException, match="Per-ticket budget"):
        router.calculate_and_audit_cost(
            model=Config.REASONING_MODEL,
            prompt_tokens=200_000,
            completion_tokens=20_000,
            accumulated_cost=0.40,
        )


def test_cost_at_ceiling_is_allowed():
    router = ModelRouter()
    total = router.calculate_and_audit_cost(
        model=Config.FAST_MODEL,
        prompt_tokens=0,
        completion_tokens=0,
        accumulated_cost=Config.PER_TICKET_COST_CEILING_USD,
    )
    assert total == Config.PER_TICKET_COST_CEILING_USD
