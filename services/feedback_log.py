"""Append-only feedback log to GCS.

Each feedback is written as a separate JSON object:
  feedback/{YYYY-MM-DD}/{uuid}.json
  {"ts": "...", "type": "...", "content": "...", "user_sub": "...",
   "question": "<回饋當下對話的最後一個問題，無則空字串>",
   "answer": "<該問題的系統答案，無則空字串>"}

No overwrite, no lock, no race — each write is independent.
If FEEDBACK_LOG_BUCKET is empty, log_feedback is a no-op.
"""

import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone

from google.cloud import storage

from config import Settings
from services.csv_safe import csv_safe


def log_feedback(
    feedback_type: str,
    content: str,
    user_sub: str,
    settings: Settings,
    storage_client=None,
    question: str = "",
    answer: str = "",
    extra: dict | None = None,
) -> None:
    """Write one feedback record to GCS. Silently does nothing if bucket not configured.

    ``extra``: 附加識別欄位（如評分記錄的 conv_id/message_ts），併入 JSON payload。
    """
    if not settings.feedback_log_bucket:
        return

    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.feedback_log_bucket)

    ts = datetime.now(timezone.utc)
    date_str = ts.strftime("%Y-%m-%d")
    record_id = uuid.uuid4().hex
    blob_name = f"feedback/{date_str}/{record_id}.json"

    payload = json.dumps(
        {
            "ts": ts.isoformat(),
            "type": feedback_type,
            "content": content,
            "user_sub": user_sub,
            "question": question,
            "answer": answer,
            **(extra or {}),
        },
        ensure_ascii=False,
    )
    bucket.blob(blob_name).upload_from_string(payload, content_type="application/json")


_RECORD_ID_RE = re.compile(r"^feedback/\d{4}-\d{2}-\d{2}/[0-9a-f]{32}\.json$")


def list_feedback_records(
    settings: Settings,
    storage_client=None,
    feedback_type: str = "",
    only_unreviewed: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """列出回饋記錄（管理後台審核用），每筆附標註狀態（review 或 None）。

    - 評分記錄（讚/倒讚）依 conv_id+message_ts 去重，取 ts 最新——改評
      up↔down 後只呈現最終狀態（GCS append-only，歷史記錄仍在 bucket）。
    - 全量讀取 + 記憶體分頁：人工回饋量級小，可接受；量大再改列舉策略。

    Raises ValueError if FEEDBACK_LOG_BUCKET is not configured.
    """
    if not settings.feedback_log_bucket:
        raise ValueError("未設定意見回饋 bucket（FEEDBACK_LOG_BUCKET）")

    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.feedback_log_bucket)

    records = []
    for blob in bucket.list_blobs(prefix="feedback/"):
        try:
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            data["record_id"] = blob.name
            records.append(data)
        except Exception:
            pass  # 跳過損壞的個別物件

    reviews: dict[str, dict] = {}
    for blob in bucket.list_blobs(prefix="reviews/"):
        try:
            r = json.loads(blob.download_as_text(encoding="utf-8"))
            reviews[r.get("record_id", "")] = r
        except Exception:
            pass

    latest: dict[tuple, dict] = {}
    plain: list[dict] = []
    for r in records:
        key = (r.get("conv_id"), r.get("message_ts"))
        if r.get("type") in ("讚", "倒讚") and all(key):
            cur = latest.get(key)
            if cur is None or r.get("ts", "") > cur.get("ts", ""):
                latest[key] = r
        else:
            plain.append(r)
    merged = plain + list(latest.values())

    if feedback_type:
        merged = [r for r in merged if r.get("type") == feedback_type]

    for r in merged:
        r["review"] = reviews.get(r["record_id"])

    if only_unreviewed:
        merged = [r for r in merged if r["review"] is None]

    merged.sort(key=lambda r: r.get("ts", ""), reverse=True)

    total = len(merged)
    start = max(page - 1, 0) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": merged[start : start + page_size],
    }


def save_feedback_review(
    settings: Settings,
    record_id: str,
    review: dict,
    storage_client=None,
) -> None:
    """寫入一筆標註到 reviews/{uuid}.json；同一 record 再標註即覆蓋更新。

    Raises ValueError（bucket 未設定或 record_id 格式無效）、
    LookupError（回饋記錄不存在）。
    """
    if not settings.feedback_log_bucket:
        raise ValueError("未設定意見回饋 bucket（FEEDBACK_LOG_BUCKET）")
    if not _RECORD_ID_RE.match(record_id):
        raise ValueError("record_id 格式無效")

    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.feedback_log_bucket)

    if not bucket.blob(record_id).exists():
        raise LookupError("回饋記錄不存在")

    uuid_part = record_id.rsplit("/", 1)[-1]
    payload = json.dumps(
        {
            "record_id": record_id,
            "correct_laws": review.get("correct_laws", ""),
            "category": review.get("category", ""),
            "note": review.get("note", ""),
            "reviewer_sub": review.get("reviewer_sub", ""),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
    )
    bucket.blob(f"reviews/{uuid_part}").upload_from_string(
        payload, content_type="application/json"
    )


def export_feedback_csv(settings: Settings, storage_client=None) -> bytes:
    """Read all feedback records from GCS and return UTF-8 BOM CSV bytes.

    Raises ValueError if FEEDBACK_LOG_BUCKET is not configured.
    Raises any GCS exception on access failure.
    """
    if not settings.feedback_log_bucket:
        raise ValueError("未設定意見回饋 bucket（FEEDBACK_LOG_BUCKET）")

    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.feedback_log_bucket)

    records = []
    for blob in bucket.list_blobs(prefix="feedback/"):
        try:
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            records.append(data)
        except Exception:
            pass  # 跳過損壞的個別物件，不中斷整批匯出

    records.sort(key=lambda r: r.get("ts", ""))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["時間", "問題類型", "內容", "對話問題", "系統答案"])
    for r in records:
        writer.writerow([
            r.get("ts", ""),
            r.get("type", ""),
            csv_safe(r.get("content", "")),
            csv_safe(r.get("question", "")),
            csv_safe(r.get("answer", "")),
        ])

    return buf.getvalue().encode("utf-8-sig")
