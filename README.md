# 法規 AI 查詢系統（AI RAG）— 公開展示版

以 FastAPI 為後端、原生 HTML/CSS/JS 為前端的法規檢索與問答系統（RAG），專為交通法規問答設計。支援共用密碼登入、角色切換、多對話管理、首頁預設問答按鈕、追問機制、意見回饋與文件管理，可部署於 Google Cloud Run。

> ## ⚠️ 這是公開展示版（Public showcase build）
>
> 本 repo 用於**展示系統架構與工程實作**，**並非可直接打包執行的完整版本**。原專案的私有內容已移除或改為範例，因此無法「clone 後直接跑起完整問答」：
>
> - **憑證與環境設定**：`.env`、真實 GCP 專案 ID / Data Store / Engine ID、API 金鑰、密碼、Service Account、bucket 名稱等**全部移除**。僅保留 `.env.example` 範本。
> - **對外呼叫已打樁（stub）**：實際呼叫 Vertex AI Search 與 Gemini 的入口（`services/client_factory.py`、`services/model_factory.py`、`services/search_service.py` 的自建 client 路徑）已改為 `raise NotImplementedError`。**架構與流程完整可讀，但實際檢索/生成需自行接上你自己的 GCP 資源。**
> - **資料**：法規語料（JSONL）、評測題庫（golden set）、完整同義詞字典均**未包含**。`data/legal_synonyms.json` 僅附 schema 範例。
> - **內部文件**：公司簡報、規格書、開發日誌、圖片、PDF 等內部素材均**未包含**。
> - **測試仍全綠**：`tests/` 與 `tests_api/` 因全數 mock 外部 API，`make test` 可通過（少數依賴已移除資料的測試標記為 `skip`）。
>
> 若要實際運行，需自備 Vertex AI Search 資源，並補回上述打樁的 client 建構邏輯。

## 功能摘要

- **法規檢索與問答（RAG pipeline）**：router → rewrite → search → answer，含複合查詢拆解與術語對照表強化
- **追問機制**：第一輪答案附數字選單（由確定性規則引擎產生），使用者回數字即走第二輪聚焦問答
- **首頁預設問答按鈕**（`presets.py`）：點擊直接取得預寫答案並可追問
- **Persona 角色切換**：交通警察（目前唯一角色）
- **多對話管理**（最多 50 個）：每個對話獨立歷史與 Persona
- **多輪對話記憶**：答案層可參考同一對話前幾輪問答
- **共用密碼登入**：`APP_PASSWORD` → 一般使用者；`ADMIN_PASSWORD` → 管理者。登入後發 JWT，每瀏覽器分到隨機 UUID，對話天然隔離
- **管理者功能**：PDF 文件上傳 / 刪除；匯出提問、意見回饋、對話歷史（CSV）
- **會話持久化**：可選記憶體 / SQLite / Firestore（正式環境用 Firestore）
- Cloud Run 部署與驗證腳本

## 專案結構

- `api/main.py`：FastAPI 入口，掛載 API routes、靜態頁面與安全標頭中介層
- `api/auth.py`：JWT 簽發與驗證、`require_admin` 把關
- `api/routes/`：auth / chat / conversation / document / persona / question / feedback 路由
- `api/{memory,session,firestore}_session_store.py`：會話狀態三種後端
- `services/`：Router、Rewriter、Search、Answer、Pipeline、Session、文件管理、提問/回饋記錄、匯出等核心服務
- `static/`：前端頁面、樣式與互動腳本
- `personas.py`：角色定義（交通警察）
- `presets.py`：首頁預設問答按鈕資料
- `config.py`：環境變數讀取
- `models.py`：Message / Conversation 資料模型

## 環境需求

- Python 3.12（部署 image 基準）
- `make`
- Google Cloud SDK
- 可用的 Vertex AI Search / Discovery Engine 資源

## 環境變數

```bash
cp .env.example .env
```

**必要欄位：**

- `VERTEX_PROJECT_ID`
- `VERTEX_DATA_STORE_ID`
- `APP_PASSWORD`
- `JWT_SECRET_KEY`（JWT 簽署金鑰，未設則啟動失敗；**須與 `APP_PASSWORD` 不同，相同時啟動會直接 raise**。產生方式：`python -c "import secrets; print(secrets.token_urlsafe(32))"`）

**選填：**

- `VERTEX_LOCATION`（預設 `global`）
- `VERTEX_INIT_LOCATION`（預設 `us-central1`）
- `VERTEX_ENGINE_ID`
- `ADMIN_PASSWORD`（空則不顯示管理功能；登入此密碼取得 admin 角色）
- `GCS_STAGING_BUCKET`（空則無法上傳文件）
- `QUESTION_LOG_BUCKET`（空則停用提問記錄與 CSV 匯出）
- `FEEDBACK_LOG_BUCKET`（空則停用意見回饋記錄與匯出）
- `SESSION_STORE_BACKEND`（`memory` | `sqlite` | `firestore`，預設 `memory`；正式環境建議 `firestore`）
- `FIRESTORE_COLLECTION`（backend 為 firestore 時使用，預設 `sessions`）

安全提醒：不要把真實密碼、專案 ID、bucket 名稱寫進文件；敏感值建議由 Secret Manager 管理。

## 本機執行

```bash
make install
make install-dev
make run
```

啟動後從 `http://localhost:8080` 存取。

## 測試

```bash
make test     # 全部測試（約 430 筆：tests/ 單元測試 + tests_api/ API 整合測試）
make check    # lint + test
```

執行單一測試檔（Windows venv）：

```bash
.venv/Scripts/python -m pytest tests/test_router_service.py -q
```

## 部署到 Cloud Run（概念）

原專案以 Docker 部署至 Cloud Run，敏感值由 Secret Manager 管理。公開版已移除含真實基礎設施資訊的部署腳本；概念上流程為：

```bash
gcloud run deploy <your-service-name> \
  --source . \
  --region <your-region> \
  --allow-unauthenticated \
  --max-instances 1 \
  --set-env-vars VERTEX_PROJECT_ID=<id>,VERTEX_DATA_STORE_ID=<id>,VERTEX_ENGINE_ID=<id>,MODEL_PROVIDER=google_genai,SESSION_STORE_BACKEND=firestore,FIRESTORE_COLLECTION=sessions \
  --set-secrets APP_PASSWORD=<secret>:latest,JWT_SECRET_KEY=<secret>:latest
```

- **`--set-env-vars` 是整組覆蓋（非合併）**：重新部署時清單漏掉的變數會被刪除（例如漏 `SESSION_STORE_BACKEND=firestore` 會讓對話退回記憶體、部署即清空）。只更新程式碼時建議不帶 env 旗標，或改用 `--update-env-vars` 合併。
- **`--max-instances 1`**：`memory` 後端狀態存於記憶體，多實例會不一致；`firestore` 後端可支援多實例。

> 注意：實際部署需先補回 `services/client_factory.py` / `services/model_factory.py` 中打樁的 client 建構邏輯（見本 README 開頭的公開版說明）。
