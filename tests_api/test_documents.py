from dataclasses import replace
from unittest.mock import patch

from tests_api.conftest import auth_header


@patch(
    "api.routes.document_routes.get_document_list_detailed",
    return_value=[{"name": "doc1", "title": "文件一"}],
)
def test_list_documents_as_admin(_mock_list, client, admin_token):
    response = client.get("/api/documents", headers=auth_header(admin_token))

    assert response.status_code == 200
    assert response.json() == [{"name": "doc1", "title": "文件一"}]


def test_list_documents_as_user(client, user_token):
    response = client.get("/api/documents", headers=auth_header(user_token))

    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理員權限"


@patch("api.routes.document_routes.upload_to_gcs", return_value="gs://bucket/test.pdf")
@patch("api.routes.document_routes.import_document_from_gcs", return_value="operations/test-op")
def test_upload_pdf(_mock_import, _mock_upload, client, admin_token):
    response = client.post(
        "/api/documents/upload",
        headers=auth_header(admin_token),
        files={"file": ("test.pdf", b"%PDF-1.7\nfake-pdf-content", "application/pdf")},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["gcs_uri"] == "gs://bucket/test.pdf"
    assert data["operation"] == "operations/test-op"


def test_upload_non_pdf(client, admin_token):
    response = client.post(
        "/api/documents/upload",
        headers=auth_header(admin_token),
        files={"file": ("test.txt", b"fake-text-content", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "僅支援 PDF 檔案"


def test_upload_no_bucket(client, admin_token):
    client.app.state.settings = replace(client.app.state.settings, gcs_staging_bucket="")

    response = client.post(
        "/api/documents/upload",
        headers=auth_header(admin_token),
        files={"file": ("test.pdf", b"%PDF-1.7\nfake-pdf-content", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "GCS staging bucket 未設定"


@patch("api.routes.document_routes.upload_to_gcs", side_effect=RuntimeError("mock upload failure"))
def test_upload_pdf_upstream_failure(_mock_upload, client, admin_token):
    response = client.post(
        "/api/documents/upload",
        headers=auth_header(admin_token),
        files={"file": ("test.pdf", b"%PDF-1.7\nfake-pdf-content", "application/pdf")},
    )

    assert response.status_code == 502
    assert "文件上傳失敗" in response.json()["detail"]
    assert "trace:" in response.json()["detail"]


# mock_settings 的 branch 前綴（project=test-project, ds=test-ds, location=global）
_VALID_DOC_ID = (
    "projects/test-project/locations/global/dataStores/test-ds/"
    "branches/default_branch/documents/1"
)


@patch("api.routes.document_routes.delete_document", return_value=None)
def test_delete_document(mock_delete, client, admin_token):
    response = client.delete(f"/api/documents/{_VALID_DOC_ID}", headers=auth_header(admin_token))

    assert response.status_code == 204
    mock_delete.assert_called_once()


@patch("api.routes.document_routes.delete_document", return_value=None)
def test_delete_document_rejects_foreign_data_store(mock_delete, client, admin_token):
    """跨 data store 的完整資源路徑應被前綴驗證擋下，回 400 且不呼叫刪除。"""
    foreign = "projects/other/locations/global/dataStores/evil/branches/default_branch/documents/1"
    response = client.delete(f"/api/documents/{foreign}", headers=auth_header(admin_token))

    assert response.status_code == 400
    mock_delete.assert_not_called()


def test_delete_document_as_user(client, user_token):
    response = client.delete(
        f"/api/documents/{_VALID_DOC_ID}",
        headers=auth_header(user_token),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理員權限"
