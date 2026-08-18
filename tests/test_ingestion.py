"""Resilient ingestion: retries, heartbeats, and DLQ alerts."""

from sra.pipeline.ingestion import IngestionAlert, IngestionWorker, KnowledgeDocument, UpsertAck


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


class _AlertRecorder:
    def __init__(self):
        self.alerts: list[IngestionAlert] = []

    def emit(self, alert: IngestionAlert) -> None:
        self.alerts.append(alert)


class _FlakyStore:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def upsert(self, collection: str, document: KnowledgeDocument) -> UpsertAck:
        self.calls += 1
        if self.calls <= self.fail_times:
            return UpsertAck(
                accepted=True,
                collection=collection,
                doc_id=document.doc_id,
                vectors_written=0,
            )
        return UpsertAck(
            accepted=True,
            collection=collection,
            doc_id=document.doc_id,
            vectors_written=3,
        )


def _doc(doc_id: str = "kb-1") -> KnowledgeDocument:
    return KnowledgeDocument(
        doc_id=doc_id,
        tenant_id="acme",
        title="Reset SMTP credentials",
        body="To reset SMTP credentials, open tenant settings and rotate the outbound token.",
        source_uri="https://kb.example/smtp",
        schema_version="v14",
        checksum="deadbeefcafebabe",
    )


def test_silent_zero_vector_upsert_retries_then_succeeds():
    store = _FlakyStore(fail_times=2)
    alerts = _AlertRecorder()
    clock = _FakeClock()
    worker = IngestionWorker(store, alerts, clock=clock)

    report = worker.run([_doc()])

    assert report.ingested == ["kb-1"]
    assert report.dead_lettered == []
    assert store.calls == 3
    assert clock.sleeps == [0.5, 1.0]


def test_exhausted_retries_go_to_dlq_with_critical_alert():
    store = _FlakyStore(fail_times=99)
    alerts = _AlertRecorder()
    worker = IngestionWorker(store, alerts, clock=_FakeClock())

    report = worker.run([_doc("kb-poison")])

    assert report.ingested == []
    assert report.dead_lettered == ["kb-poison"]
    assert any(alert.code == "INGESTION_DLQ" and alert.severity == "critical" for alert in alerts.alerts)
    assert worker.dead_letters[0].document.doc_id == "kb-poison"


def test_unsupported_schema_is_dead_lettered_without_upsert():
    class _NoopStore:
        def upsert(self, collection: str, document: KnowledgeDocument) -> UpsertAck:
            raise AssertionError("upsert should not run for unsupported schema")

    alerts = _AlertRecorder()
    worker = IngestionWorker(_NoopStore(), alerts, clock=_FakeClock())
    stale = _doc("kb-old").model_copy(update={"schema_version": "v13"})

    report = worker.run([stale])

    assert report.dead_lettered == ["kb-old"]
    assert report.heartbeats >= 1
