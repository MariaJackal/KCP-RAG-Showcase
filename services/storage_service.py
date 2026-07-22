"""GCS upload/delete for document staging."""

from google.cloud import storage

from config import Settings


def upload_to_gcs(file_bytes, filename, settings, storage_client=None):
    """Upload file bytes to GCS staging bucket. Returns gs:// URI."""
    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.gcs_staging_bucket)
    blob = bucket.blob(filename)
    blob.upload_from_string(file_bytes, content_type="application/pdf")
    return f"gs://{settings.gcs_staging_bucket}/{filename}"


def delete_from_gcs(filename, settings, storage_client=None):
    """Delete a file from GCS staging bucket."""
    client = storage_client or storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.gcs_staging_bucket)
    blob = bucket.blob(filename)
    blob.delete()
