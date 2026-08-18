"""Groundedness interceptor and human fallback gate."""

from sra.middleware.guardrail import EscalationReason, GuardrailInterceptor


CONTEXT = [
    "To rotate the SMTP token, open Tenant Settings, choose Email, then click Rotate token.",
    "The new token takes effect within five minutes and invalidates the previous credential.",
]


def test_grounded_answer_is_allowed():
    interceptor = GuardrailInterceptor()
    decision = interceptor.intercept(
        "Open Tenant Settings, choose Email, and click Rotate token. The new token takes effect within five minutes.",
        CONTEXT,
    )
    assert decision.allow is True
    assert decision.escalate_to_human is False
    assert decision.reason is EscalationReason.GROUNDED
    assert decision.groundedness >= 0.88


def test_hallucinated_answer_is_blocked_and_escalated():
    interceptor = GuardrailInterceptor()
    decision = interceptor.intercept(
        "Delete the production database and restart every cluster node immediately.",
        CONTEXT,
    )
    assert decision.allow is False
    assert decision.escalate_to_human is True
    assert decision.reason is EscalationReason.LOW_GROUNDEDNESS
    assert "support specialist" in decision.customer_message


def test_missing_retrieval_context_escalates():
    interceptor = GuardrailInterceptor()
    decision = interceptor.intercept("Any answer at all.", [])
    assert decision.allow is False
    assert decision.reason is EscalationReason.EMPTY_CONTEXT
