"""Append-only question log to GCS.

Each question is written as a separate JSON object:
  questions/{YYYY-MM-DD}/{uuid}.json
  {"ts": "...", "question": "...", "user_sub": "..."}

No overwrite, no lock, no race — each write is independent.
If QUESTION_LOG_BUCKET is empty, log_question is a no-op.
"""

import csv
import io
import json
import uuid
from datetime import datetime, timezone

from google.cloud import storage

from config import Settings
from services.csv_safe import csv_safe


def log_question(question: str, user_sub: str, settings: Settings, storage_client=None) -> None:
    """Write one question record to GCS. Silently does nothing if bucket not configured."""
    if not settings.question_log_bucket:
        return

    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.question_log_bucket)

    ts = datetime.now(timezone.utc)
    date_str = ts.strftime("%Y-%m-%d")
    record_id = uuid.uuid4().hex
    blob_name = f"questions/{date_str}/{record_id}.json"

    payload = json.dumps(
        {"ts": ts.isoformat(), "question": question, "user_sub": user_sub},
        ensure_ascii=False,
    )
    bucket.blob(blob_name).upload_from_string(payload, content_type="application/json")


def export_questions_csv(settings: Settings, storage_client=None) -> bytes:
    """Read all question records from GCS and return UTF-8 BOM CSV bytes.

    Raises ValueError if QUESTION_LOG_BUCKET is not configured.
    Raises any GCS exception on access failure.
    """
    if not settings.question_log_bucket:
        raise ValueError("未設定提問記錄 bucket（QUESTION_LOG_BUCKET）")

    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.question_log_bucket)

    records = []
    for blob in bucket.list_blobs(prefix="questions/"):
        try:
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            records.append(data)
        except Exception:
            pass  # 跳過損壞的個別物件，不中斷整批匯出

    records.sort(key=lambda r: r.get("ts", ""))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["時間", "問題", "使用者"])
    for r in records:
        writer.writerow([r.get("ts", ""), csv_safe(r.get("question", "")), csv_safe(r.get("user_sub", ""))])

    return buf.getvalue().encode("utf-8-sig")
