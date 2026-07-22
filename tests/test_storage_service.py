from config import Settings
from services.storage_service import delete_from_gcs, upload_to_gcs


def _settings():
    return Settings(
        project_id="p",
        data_store_id="d",
        location="global",
        vertex_init_location="us-central1",
        app_password="pw",
        admin_password="admin",
        gcs_staging_bucket="test-bucket",
    )


class _MockBlob:
    def __init__(self):
        self.uploaded_data = None
        self.uploaded_content_type = None
        self.deleted = False

    def upload_from_string(self, data, content_type=None):
        self.uploaded_data = data
        self.uploaded_content_type = content_type

    def delete(self):
        self.deleted = True


class _MockBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, name):
        b = _MockBlob()
        self.blobs[name] = b
        return b


class _MockStorageClient:
    def __init__(self):
        self._bucket = _MockBucket()

    def bucket(self, name):
        self._bucket_name = name
        return self._bucket


def test_upload_to_gcs_returns_uri():
    client = _MockStorageClient()
    uri = upload_to_gcs(b"pdf-content", "test.pdf", _settings(), storage_client=client)
    assert uri == "gs://test-bucket/test.pdf"
    blob = client._bucket.blobs["test.pdf"]
    assert blob.uploaded_data == b"pdf-content"
    assert blob.uploaded_content_type == "application/pdf"


def test_delete_from_gcs():
    client = _MockStorageClient()
    delete_from_gcs("test.pdf", _settings(), storage_client=client)
    blob = client._bucket.blobs["test.pdf"]
    assert blob.deleted is True
