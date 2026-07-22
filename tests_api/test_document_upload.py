"""#14: PDF 上傳須驗證內容 magic bytes，僅副檔名不足以信任。"""

from unittest.mock import patch

from tests_api.conftest import auth_header


def test_rejects_non_pdf_extension(client, admin_token):
    r = client.post(
        "/api/documents/upload",
        files={"file": ("evil.txt", b"hello", "text/plain")},
        headers=auth_header(admin_token),
    )
    assert r.status_code == 400


def test_rejects_pdf_extension_with_non_pdf_content(client, admin_token):
    r = client.post(
        "/api/documents/upload",
        files={"file": ("fake.pdf", b"<html>not a pdf</html>", "application/pdf")},
        headers=auth_header(admin_token),
    )
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_accepts_valid_pdf(client, admin_token):
    with patch("api.routes.document_routes.upload_to_gcs", return_value="gs://b/real.pdf") as up:
        with patch("api.routes.document_routes.import_document_from_gcs", return_value="op-1"):
            r = client.post(
                "/api/documents/upload",
                files={"file": ("real.pdf", b"%PDF-1.7\n%binary\n...", "application/pdf")},
                headers=auth_header(admin_token),
            )
    assert r.status_code == 201
    up.assert_called_once()
