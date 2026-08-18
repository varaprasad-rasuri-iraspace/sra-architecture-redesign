"""
Evaluation guardrails and groundedness interceptor.

Answers that are not sufficiently supported by retrieved knowledge are blocked
inline and escalated to human support instead of being shown to the customer.
"""
from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from sra.config import Config

logger = logging.getLogger("sra.middleware.guardrail")

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "you",
        "your",
        "this",
        "that",
        "with",
        "from",
        "have",
        "has",
        "are",
        "was",
        "were",
        "will",
        "can",
        "please",
        "error",
        "issue",
    }
)


class EscalationReason(str, Enum):
    GROUNDED = "grounded"
    LOW_GROUNDEDNESS = "low_groundedness"
    EMPTY_CONTEXT = "empty_context"
    EMPTY_ANSWER = "empty_answer"


class GuardrailDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: bool
    groundedness: float = Field(..., ge=0.0, le=1.0)
    reason: EscalationReason
    customer_message: str
    escalate_to_human: bool


class GroundednessEvaluator:
    """
    Deterministic groundedness scorer used as the inline interceptor.

    Token overlap against retrieved passages is the default production check
    (fast, no extra model spend). An LLM-as-judge can replace `score` without
    changing the interceptor contract.
    """

    def score(self, answer: str, contexts: Iterable[str]) -> float:
        answer_tokens = self._tokens(answer)
        if not answer_tokens:
            return 0.0

        context_tokens: set[str] = set()
        for passage in contexts:
            context_tokens.update(self._tokens(passage))
        if not context_tokens:
            return 0.0

        overlap = answer_tokens & context_tokens
        return round(len(overlap) / len(answer_tokens), 4)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS}


class GuardrailInterceptor:
    HUMAN_FALLBACK_MESSAGE = (
        "I want to make sure this is accurate. A support specialist is reviewing "
        "your ticket and will follow up shortly."
    )

    def __init__(
        self,
        evaluator: GroundednessEvaluator | None = None,
        *,
        config: Config | None = None,
    ):
        self.evaluator = evaluator or GroundednessEvaluator()
        self.config = config or Config()

    def intercept(self, answer: str, contexts: list[str]) -> GuardrailDecision:
        cleaned_answer = (answer or "").strip()
        if not cleaned_answer:
            return self._escalate(0.0, EscalationReason.EMPTY_ANSWER)

        usable_contexts = [ctx.strip() for ctx in contexts if ctx and ctx.strip()]
        if not usable_contexts:
            return self._escalate(0.0, EscalationReason.EMPTY_CONTEXT)

        score = self.evaluator.score(cleaned_answer, usable_contexts)
        if score < self.config.TARGET_GROUNDEDNESS_THRESHOLD:
            return self._escalate(score, EscalationReason.LOW_GROUNDEDNESS)

        logger.info("Groundedness passed score=%.4f", score)
        return GuardrailDecision(
            allow=True,
            groundedness=score,
            reason=EscalationReason.GROUNDED,
            customer_message=cleaned_answer,
            escalate_to_human=False,
        )

    def _escalate(self, score: float, reason: EscalationReason) -> GuardrailDecision:
        logger.warning(
            "Guardrail blocked answer reason=%s score=%.4f threshold=%.2f",
            reason.value,
            score,
            self.config.TARGET_GROUNDEDNESS_THRESHOLD,
        )
        return GuardrailDecision(
            allow=False,
            groundedness=score,
            reason=reason,
            customer_message=self.HUMAN_FALLBACK_MESSAGE,
            escalate_to_human=True,
        )
