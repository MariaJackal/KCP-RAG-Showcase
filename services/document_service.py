from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import PermissionDenied
from google.auth.exceptions import DefaultCredentialsError

from config import Settings
from rag_logic import extract_result_title


class _ListDocumentsRequestShim:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _DeleteDocumentRequestShim:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _GcsSourceShim:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _ImportDocumentsRequestShim:
    class ReconciliationMode:
        INCREMENTAL = "INCREMENTAL"

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _load_discoveryengine():
    try:
        from google.cloud import discoveryengine_v1 as discoveryengine
    except Exception:
        return None
    return discoveryengine


def _document_metadata(doc):
    if getattr(doc, "struct_data", None):
        return doc.struct_data
    if getattr(doc, "derived_struct_data", None):
        return doc.derived_struct_data
    return {}


def _document_title(doc):
    data = _document_metadata(doc)
    title = extract_result_title(data, default="")
    if title:
        return title

    content_uri = doc.content.uri if hasattr(doc.content, "uri") else ""
    return content_uri.split("/")[-1] if "/" in content_uri else "未命名文件"


def get_document_list(settings: Settings, document_client=None):
    """Get indexed document titles from Discovery Engine."""
    try:
        discoveryengine = _load_discoveryengine()
        client = document_client
        if client is None:
            if discoveryengine is None:
                raise RuntimeError("google-cloud-discoveryengine 套件不可用，無法建立 DocumentServiceClient")
            client_options = (
                ClientOptions(api_endpoint=f"{settings.location}-discoveryengine.googleapis.com")
                if settings.location != "global"
                else None
            )
            client = discoveryengine.DocumentServiceClient(client_options=client_options)

        branch_path = (
            f"projects/{settings.project_id}/locations/{settings.location}/"
            f"dataStores/{settings.data_store_id}/branches/default_branch"
        )

        request_cls = discoveryengine.ListDocumentsRequest if discoveryengine else _ListDocumentsRequestShim
        request = request_cls(parent=branch_path, page_size=100)
        page_result = client.list_documents(request=request)

        doc_names = []
        for doc in page_result:
            doc_names.append(_document_title(doc))

        return sorted(list(set(doc_names)))
    except DefaultCredentialsError:
        return ["無法連線 GCP：請先設定 Application Default Credentials (ADC)。"]
    except PermissionDenied:
        return ["權限不足：請確認 Discovery Engine 與 IAM 權限設定。"]
    except Exception as e:
        return [f"讀取失敗: {str(e)}"]


def _branch_path(settings):
    return (
        f"projects/{settings.project_id}/locations/{settings.location}/"
        f"dataStores/{settings.data_store_id}/branches/default_branch"
    )


def get_document_list_detailed(settings, document_client):
    """Return list of dicts with 'name' (resource name) and 'title'."""
    try:
        discoveryengine = _load_discoveryengine()
        request_cls = discoveryengine.ListDocumentsRequest if discoveryengine else _ListDocumentsRequestShim
        request = request_cls(
            parent=_branch_path(settings), page_size=100
        )
        page_result = document_client.list_documents(request=request)

        docs = []
        for doc in page_result:
            docs.append({"name": doc.name, "title": _document_title(doc)})
        return docs
    except Exception as e:
        return [{"name": "", "title": f"讀取失敗: {str(e)}"}]


def import_document_from_gcs(gcs_uri, settings, document_client):
    """Trigger async Discovery Engine import from GCS. Returns operation name."""
    discoveryengine = _load_discoveryengine()
    request_cls = discoveryengine.ImportDocumentsRequest if discoveryengine else _ImportDocumentsRequestShim
    gcs_source_cls = discoveryengine.GcsSource if discoveryengine else _GcsSourceShim
    reconciliation_mode = (
        discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL
        if discoveryengine
        else _ImportDocumentsRequestShim.ReconciliationMode.INCREMENTAL
    )
    request = request_cls(
        parent=_branch_path(settings),
        gcs_source=gcs_source_cls(
            input_uris=[gcs_uri],
            data_schema="content",
        ),
        reconciliation_mode=reconciliation_mode,
    )
    operation = document_client.import_documents(request=request)
    return operation.operation.name


def delete_document(document_name, settings, document_client):
    """Delete a document by its resource name."""
    discoveryengine = _load_discoveryengine()
    request_cls = discoveryengine.DeleteDocumentRequest if discoveryengine else _DeleteDocumentRequestShim
    request = request_cls(name=document_name)
    document_client.delete_document(request=request)

