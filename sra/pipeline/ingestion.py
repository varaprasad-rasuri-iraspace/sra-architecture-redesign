"""
Resilient knowledge ingestion worker.

Post-v14, a silent upsert failure left the vector collection stale while the
worker continued reporting success. This worker requires an explicit write ack,
emits heartbeats, retries with exponential backoff, and dead-letters poison
documents with an alert instead of swallowing the error.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sra.config import Config

logger = logging.getLogger("sra.pipeline.ingestion")


class IngestionError(Exception):
    """Raised when a document cannot be ingested after policy checks."""


class VectorStore(Protocol):
    def upsert(self, collection: str, document: "KnowledgeDocument") -> "UpsertAck": ...


class AlertSink(Protocol):
    def emit(self, alert: "IngestionAlert") -> None: ...


class Clock(Protocol):
    def sleep(self, seconds: float) -> None: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=32)
    source_uri: str = Field(..., min_length=1)
    schema_version: str = Field(..., min_length=1)
    checksum: str = Field(..., min_length=8)

    @field_validator("body")
    @classmethod
    def _non_placeholder(cls, value: str) -> str:
        if value.strip().lower() in {"todo", "tbd", "n/a"}:
            raise ValueError("document body is a placeholder and cannot be indexed")
        return value


class UpsertAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    collection: str
    doc_id: str
    vectors_written: int = Field(..., ge=0)


class IngestionAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    message: str
    doc_id: str | None = None
    tenant_id: str | None = None
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DeadLetterRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    document: KnowledgeDocument
    error: str
    attempts: int
    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class IngestionReport:
    ingested: list[str] = field(default_factory=list)
    dead_lettered: list[str] = field(default_factory=list)
    heartbeats: int = 0


class IngestionWorker:
    """
    Ingests knowledge documents with retries, heartbeats, and DLQ alerts.

    A document is only counted as success when the vector store returns
    `accepted=True` and `vectors_written > 0`. That closes the v14 silent-failure
    path where a no-op upsert was treated as healthy.
    """

    def __init__(
        self,
        vector_store: VectorStore,
        alert_sink: AlertSink,
        *,
        config: Config | None = None,
        clock: Clock | None = None,
        dead_letters: list[DeadLetterRecord] | None = None,
    ):
        self.vector_store = vector_store
        self.alert_sink = alert_sink
        self.config = config or Config()
        self.clock = clock or SystemClock()
        self.dead_letters = dead_letters if dead_letters is not None else []
        self._last_heartbeat = 0.0

    def run(self, documents: list[KnowledgeDocument]) -> IngestionReport:
        report = IngestionReport()
        self._heartbeat(report, force=True)

        for document in documents:
            self._heartbeat(report)
            if document.schema_version != "v14":
                self._dead_letter(
                    document,
                    f"Unsupported schema_version={document.schema_version}; expected v14",
                    attempts=0,
                )
                report.dead_lettered.append(document.doc_id)
                continue

            try:
                self._ingest_with_retry(document)
                report.ingested.append(document.doc_id)
            except IngestionError as exc:
                self._dead_letter(document, str(exc), attempts=self.config.INGESTION_MAX_RETRIES)
                report.dead_lettered.append(document.doc_id)

        self._heartbeat(report, force=True)
        return report

    def _ingest_with_retry(self, document: KnowledgeDocument) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self.config.INGESTION_MAX_RETRIES + 1):
            try:
                ack = self.vector_store.upsert(self.config.VECTOR_DB_COLLECTION, document)
                if not ack.accepted or ack.vectors_written <= 0:
                    raise IngestionError(
                        f"Silent upsert failure for {document.doc_id}: "
                        f"accepted={ack.accepted} vectors_written={ack.vectors_written}"
                    )
                if ack.doc_id != document.doc_id:
                    raise IngestionError(
                        f"Upsert ack doc_id mismatch: expected {document.doc_id} got {ack.doc_id}"
                    )
                logger.info(
                    "Ingested doc=%s tenant=%s collection=%s vectors=%s attempt=%s",
                    document.doc_id,
                    document.tenant_id,
                    ack.collection,
                    ack.vectors_written,
                    attempt,
                )
                return
            except IngestionError as exc:
                last_error = exc
                logger.warning("Ingestion attempt %s failed for %s: %s", attempt, document.doc_id, exc)
                if attempt < self.config.INGESTION_MAX_RETRIES:
                    backoff = self.config.INGESTION_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                    self.clock.sleep(backoff)

        raise IngestionError(str(last_error) if last_error else "Unknown ingestion failure")

    def _dead_letter(self, document: KnowledgeDocument, error: str, attempts: int) -> None:
        record = DeadLetterRecord(
            record_id=uuid4().hex,
            document=document,
            error=error,
            attempts=attempts,
        )
        self.dead_letters.append(record)
        alert = IngestionAlert(
            severity="critical",
            code="INGESTION_DLQ",
            message=error,
            doc_id=document.doc_id,
            tenant_id=document.tenant_id,
        )
        self.alert_sink.emit(alert)
        logger.error("Dead-lettered doc=%s tenant=%s error=%s", document.doc_id, document.tenant_id, error)

    def _heartbeat(self, report: IngestionReport, *, force: bool = False) -> None:
        now = self.clock.monotonic()
        interval = self.config.INGESTION_HEARTBEAT_INTERVAL_SECONDS
        if force or (now - self._last_heartbeat) >= interval:
            self.alert_sink.emit(
                IngestionAlert(
                    severity="info",
                    code="INGESTION_HEARTBEAT",
                    message="Ingestion worker alive",
                )
            )
            self._last_heartbeat = now
            report.heartbeats += 1
