# Support Resolution Agent (SRA) — Architecture Redesign & Extension

## Overview
This repository contains the production-grade code artifacts, dynamic model routing middleware, security guardrails, and ingestion pipeline fixes for the **Support Resolution Agent (SRA)** as part of the Principal AI-Native Engineer assessment for Ionic Partners.

### Key Problem Statements Addressed
1. **Quality Degradation Fix:** Diagnosed silent ingestion pipeline failure post-v14 release (causing troubleshooting accuracy to drop from 86% to 71%) and implemented a resilient ingestion engine with alerting and exponential backoff.
2. **Safe Tool Extension:** Transitioned SRA from read-only to write/remediation capabilities using modular, MCP-style action tools with Pydantic validation, tenant context isolation, and least-privilege scoping.
3. **13x Scale Cost Controls:** Introduced a dynamic model router that routes 80% of routine queries to fast, low-cost models (`gpt-4o-mini` / `claude-3-haiku`), capping mean ticket cost at **<$0.12** at 31,000 tickets/month.
4. **Production Guardrails:** Added inline answer groundedness evaluation middleware to intercept hallucinations and trigger automated human support escalation.

---

## Directory Structure
- `sra/config.py`: Cost parameters, budget boundaries, and model tier configurations.
- `sra/router.py`: Dynamic model router and real-time per-ticket cost tracking middleware.
- `sra/tools/`: Security-bound action plugins (MCP pattern) with tenant and session token isolation.
- `sra/pipeline/`: Resilient knowledge ingestion worker with retries, heartbeats, and Dead Letter Queue (DLQ) alerts.
- `sra/middleware/`: Groundedness evaluation interceptor and human fallback gate.

## Quick Start

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```
