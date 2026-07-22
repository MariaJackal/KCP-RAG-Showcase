"""Export Firestore conversation records as CSV bytes.

Each row = one (使用者問題 → 系統答案) pair; a conversation with N turns is
flattened into N rows. Empty conversations are skipped.

Mirrors services/question_log.export_questions_csv: returns UTF-8-BOM CSV bytes
so Excel opens it with correct Chinese and split columns.
"""

import csv
import io

from config import Settings
from services.csv_safe import csv_safe

HEADERS = ["使用者ID", "對話標題", "對話建立時間", "使用者問題", "系統答案", "答案時間"]


def _flatten(conversations: dict):
    """Yield rows from one user's conversations dict (raw Firestore maps)."""
    for conv in (conversations or {}).values():
        messages = conv.get("messages", [])
        if not messages:
            continue
        title = csv_safe(conv.get("title", ""))
        created = conv.get("created_at", "")
        pending_q = None
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                pending_q = content
            elif role == "assistant" and pending_q is not None:
                yield [title, created, csv_safe(pending_q), csv_safe(content), m.get("timestamp", "")]
                pending_q = None
        if pending_q is not None:
            yield [title, created, csv_safe(pending_q), "", ""]


def export_conversations_csv(settings: Settings, firestore_client=None) -> bytes:
    """Read all conversation records from Firestore and return UTF-8 BOM CSV bytes."""
    client = firestore_client
    if client is None:
        from google.cloud import firestore
        client = firestore.Client(project=settings.project_id)

    collection = client.collection(settings.firestore_collection)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADERS)
    for doc in collection.stream():
        data = doc.to_dict() or {}
        for title, created, question, answer, ans_ts in _flatten(data.get("conversations")):
            writer.writerow([doc.id, title, created, question, answer, ans_ts])

    return buf.getvalue().encode("utf-8-sig")
