import json
from datetime import datetime, timezone

from afuture.journal import AuditJournal
from afuture.models import Offset, OrderRequest, OrderSide


def test_audit_journal_serializes_dataclasses_enums_and_datetime(tmp_path):
    path = tmp_path / "audit.jsonl"
    journal = AuditJournal(path)
    journal.record(
        "order_request",
        OrderRequest("m2609", "DCE", OrderSide.BUY, Offset.OPEN, 1, 3000.0),
        timestamp=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["event_type"] == "order_request"
    assert row["payload"]["side"] == "BUY"
