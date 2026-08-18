"""
Dynamic Model Router Middleware.
Inspects query intent and routes 80% of routine traffic to fast models,
reserving high-tier reasoning models for complex troubleshooting.
Track and enforce per-ticket cost bounds in real-time.
"""
import logging

from sra.config import Config, ModelTier

logger = logging.getLogger("sra.router")


class CostExceededException(Exception):
    """Raised when an agent execution exceeds the designated cost ceiling."""

    pass


class ModelRouter:
    def __init__(self):
        self.config = Config()

    def select_model(self, query_category: str, historical_loop_count: int) -> ModelTier:
        """
        Dynamically selects LLM tier based on ticket categorization and iteration depth.
        """
        # Complex multi-step troubleshooting or repeated loops escalate to Sonnet
        if query_category in ["error_troubleshooting", "action_remediation"] or historical_loop_count > 1:
            logger.info(f"Escalating ticket to REASONING_MODEL ({self.config.REASONING_MODEL.name})")
            return self.config.REASONING_MODEL

        # Standard informational and basic configuration queries use low-cost tier
        logger.info(f"Routing ticket to FAST_MODEL ({self.config.FAST_MODEL.name})")
        return self.config.FAST_MODEL

    def calculate_and_audit_cost(
        self,
        model: ModelTier,
        prompt_tokens: int,
        completion_tokens: int,
        accumulated_cost: float,
    ) -> float:
        """
        Calculates execution step cost and enforces hard per-ticket budget limit.
        """
        step_cost = (
            (prompt_tokens / 1000.0) * model.input_cost_per_1k
            + (completion_tokens / 1000.0) * model.output_cost_per_1k
        )
        total_cost = accumulated_cost + step_cost

        logger.debug(f"Step Cost: ${step_cost:.5f} | Total Ticket Cost: ${total_cost:.5f}")

        if total_cost > self.config.PER_TICKET_COST_CEILING_USD:
            logger.error(
                f"Cost Limit Exceeded: ${total_cost:.4f} > Ceiling ${self.config.PER_TICKET_COST_CEILING_USD}"
            )
            raise CostExceededException(
                f"Execution terminated: Per-ticket budget of ${self.config.PER_TICKET_COST_CEILING_USD} reached."
            )

        return total_cost
