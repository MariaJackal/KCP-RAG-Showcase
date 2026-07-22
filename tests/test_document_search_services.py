from google.api_core.exceptions import PermissionDenied
from google.auth.exceptions import DefaultCredentialsError

from config import Settings
from services.document_service import (
    delete_document,
    get_document_list,
    get_document_list_detailed,
    import_document_from_gcs,
)
from services.search_service import search_vertex


class _Doc:
    def __init__(self, title=None, uri="", name="", struct_data=None):
        self.derived_struct_data = {"title": title} if title is not None else {}
        self.struct_data = struct_data or {}
        self.name = name

        class _Content:
            def __init__(self, u):
                self.uri = u

        self.content = _Content(uri)


class _Operation:
    class _Inner:
        def __init__(self):
            self.name = "operations/test-op-123"
    def __init__(self):
        self.operation = self._Inner()


class _DocumentClient:
    def __init__(self, docs=None, error=None):
        self.docs = docs or []
        self.error = error
        self.deleted_names = []
        self.imported_requests = []

    def list_documents(self, request):
        if self.error:
            raise self.error
        return self.docs

    def import_documents(self, request):
        self.imported_requests.append(request)
        return _Operation()

    def delete_document(self, request):
        self.deleted_names.append(request.name)
        return None


class _SearchClient:
    def __init__(self):
        self.calls = []

    def search(self, request):
        self.calls.append(request)
        return {"ok": True, "query": request.query}


def _settings():
    return Settings(
        project_id="p",
        data_store_id="d",
        location="global",
        vertex_init_location="us-central1",
        app_password="pw",
    )


def test_get_document_list_dedup_sort_and_fallback_uri_title():
    docs = [
        _Doc(title="B 瘜?", uri="gs://a/b.pdf"),
        _Doc(title="A 瘜?", uri="gs://a/c.pdf"),
        _Doc(title="A 瘜?", uri="gs://a/d.pdf"),
        _Doc(title=None, uri="gs://bucket/unknown.txt"),
    ]
    out = get_document_list(_settings(), document_client=_DocumentClient(docs=docs))
    assert out == ["A 瘜?", "B 瘜?", "unknown.txt"]


def test_get_document_list_handles_missing_adc():
    out = get_document_list(
        _settings(), document_client=_DocumentClient(error=DefaultCredentialsError("adc"))
    )
    assert out and "ADC" in out[0]


def test_get_document_list_handles_permission_denied():
    out = get_document_list(
        _settings(), document_client=_DocumentClient(error=PermissionDenied("denied"))
    )
    assert out and "權限不足" in out[0]


def test_get_document_list_handles_generic_exception():
    out = get_document_list(_settings(), document_client=_DocumentClient(error=RuntimeError("x")))
    assert out and "讀取失敗" in out[0]


def test_search_vertex_uses_injected_client_and_returns_search_result():
    client = _SearchClient()
    out = search_vertex("??", _settings(), search_client=client)
    assert out["ok"] is True
    assert out["query"] == "??"
    assert len(client.calls) == 1


def test_get_document_list_detailed_returns_names_and_titles():
    docs = [
        _Doc(title="瘜?A", uri="gs://a/a.pdf", name="projects/p/locations/global/dataStores/d/branches/default_branch/documents/1"),
        _Doc(title=None, uri="gs://bucket/file.pdf", name="projects/p/locations/global/dataStores/d/branches/default_branch/documents/2"),
    ]
    out = get_document_list_detailed(_settings(), _DocumentClient(docs=docs))
    assert len(out) == 2
    assert out[0]["title"] == "瘜?A"
    assert "documents/1" in out[0]["name"]
    assert out[1]["title"] == "file.pdf"


def test_get_document_list_detailed_handles_error():
    out = get_document_list_detailed(_settings(), _DocumentClient(error=RuntimeError("fail")))
    assert len(out) == 1
    assert "讀取失敗" in out[0]["title"]


def test_get_document_list_supports_structured_doc_titles():
    docs = [
        _Doc(
            struct_data={
                "law_name": "道路交通管理處罰條例",
                "display_name": "第 5 條",
            }
        )
    ]
    out = get_document_list(_settings(), document_client=_DocumentClient(docs=docs))
    assert out == ["道路交通管理處罰條例 第 5 條"]


def test_import_document_from_gcs_returns_operation_name():
    client = _DocumentClient()
    op = import_document_from_gcs("gs://bucket/test.pdf", _settings(), client)
    assert op == "operations/test-op-123"
    assert len(client.imported_requests) == 1
    request = client.imported_requests[0]
    assert request.gcs_source.input_uris == ["gs://bucket/test.pdf"]
    assert request.gcs_source.data_schema == "content"


def test_delete_document():
    client = _DocumentClient()
    doc_name = "projects/p/locations/global/dataStores/d/branches/default_branch/documents/1"
    delete_document(doc_name, _settings(), client)
    assert doc_name in client.deleted_names


