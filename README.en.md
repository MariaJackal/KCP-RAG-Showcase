# Legal Regulatory AI Query System (AI RAG) — Public Showcase Build

**English** · [繁體中文](README.md)

A retrieval-augmented generation (RAG) system for legal Q&A, built with a FastAPI backend and a vanilla HTML/CSS/JS frontend, designed for traffic-law questions. It supports shared-password login, persona switching, multi-conversation management, homepage preset Q&A buttons, a clarification (follow-up) flow, user feedback, and document management. Deployable to Google Cloud Run.

> ## ⚠️ This is a public showcase build
>
> This repository exists to **demonstrate the system's architecture and engineering**, and is **not a ready-to-run, packageable copy of the full product**. The original project's private content has been removed or replaced with samples, so you cannot "clone and immediately run full Q&A":
>
> - **Credentials & environment config**: `.env`, real GCP project ID / Data Store / Engine ID, API keys, passwords, service accounts, bucket names, etc. are **all removed**. Only the `.env.example` template remains.
> - **External calls are stubbed**: the entry points that actually call Vertex AI Search and Gemini (the self-constructing client paths in `services/client_factory.py`, `services/model_factory.py`, and `services/search_service.py`) now `raise NotImplementedError`. **The architecture and flow remain fully readable, but real retrieval/generation requires wiring up your own GCP resources.**
> - **Data**: the legal corpus (JSONL), the evaluation golden set, and the full synonym dictionary are **not included**. `data/legal_synonyms.json` ships with a schema sample only.
> - **Internal documents**: company slide decks, spec sheets, dev logs, images, and PDFs are **not included**.
> - **Tests still pass**: because `tests/` and `tests_api/` mock all external APIs, `make test` passes (a few tests that depend on removed data are marked `skip`).
>
> To actually run it, you must supply your own Vertex AI Search resources and restore the stubbed client-construction logic described above.

## Feature Overview

- **Legal retrieval & Q&A (RAG pipeline)**: router → rewrite → search → answer, with composite-query decomposition and a terminology-mapping reinforcement layer
- **Follow-up (clarification) flow**: first-round answers append a numbered menu (produced by a deterministic rule engine); replying with a digit routes to a focused second-round answer
- **Homepage preset Q&A buttons** (`presets.py`): click to get a pre-written answer, then optionally follow up
- **Persona switching**: traffic officer (the only persona for now)
- **Multi-conversation management** (up to 50): each conversation has its own history and persona
- **Multi-turn memory**: the answer layer can reference earlier turns within the same conversation
- **Shared-password login**: `APP_PASSWORD` → regular user; `ADMIN_PASSWORD` → admin. A JWT is issued on login, each browser gets a random UUID, and conversations are naturally isolated
- **Admin features**: PDF document upload / deletion; export of questions, feedback, and conversation history (CSV)
- **Session persistence**: pluggable memory / SQLite / Firestore (Firestore in production)
- Cloud Run deployment and verification workflow

## Project Structure

- `api/main.py`: FastAPI entry point; mounts API routes, static pages, and the security-headers middleware
- `api/auth.py`: JWT issuance/verification and the `require_admin` gate
- `api/routes/`: auth / chat / conversation / document / persona / question / feedback routes
- `api/{memory,session,firestore}_session_store.py`: three session-state backends
- `services/`: core services — Router, Rewriter, Search, Answer, Pipeline, Session, document management, question/feedback logging, exports, etc.
- `static/`: frontend pages, styles, and interaction scripts
- `personas.py`: persona definitions (traffic officer)
- `presets.py`: homepage preset Q&A button data
- `config.py`: environment-variable loading
- `models.py`: Message / Conversation data models

## Requirements

- Python 3.12 (matches the deployment image)
- `make`
- Google Cloud SDK
- Working Vertex AI Search / Discovery Engine resources

## Environment Variables

```bash
cp .env.example .env
```

**Required:**

- `VERTEX_PROJECT_ID`
- `VERTEX_DATA_STORE_ID`
- `APP_PASSWORD`
- `JWT_SECRET_KEY` (JWT signing key; startup fails if unset. **Must differ from `APP_PASSWORD`** — startup raises if they match. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`)

**Optional:**

- `VERTEX_LOCATION` (default `global`)
- `VERTEX_INIT_LOCATION` (default `us-central1`)
- `VERTEX_ENGINE_ID`
- `ADMIN_PASSWORD` (empty = admin features hidden; log in with this password to obtain the admin role)
- `GCS_STAGING_BUCKET` (empty = document upload disabled)
- `QUESTION_LOG_BUCKET` (empty = question logging and CSV export disabled)
- `FEEDBACK_LOG_BUCKET` (empty = feedback logging and export disabled)
- `SESSION_STORE_BACKEND` (`memory` | `sqlite` | `firestore`; default `memory`; `firestore` recommended in production)
- `FIRESTORE_COLLECTION` (used when backend is `firestore`; default `sessions`)

Security note: never put real passwords, project IDs, or bucket names in documentation; keep sensitive values in Secret Manager.

## Running Locally

```bash
make install
make install-dev
make run
```

Then open `http://localhost:8080`.

## Testing

```bash
make test     # full suite (~430 tests: tests/ unit tests + tests_api/ API integration tests)
make check    # lint + test
```

Run a single test file (Windows venv):

```bash
.venv/Scripts/python -m pytest tests/test_router_service.py -q
```

## Deploying to Cloud Run (conceptual)

The original project deploys to Cloud Run via Docker, with sensitive values managed by Secret Manager. The public build removes deployment scripts that contained real infrastructure identifiers; conceptually the flow is:

```bash
gcloud run deploy <your-service-name> \
  --source . \
  --region <your-region> \
  --allow-unauthenticated \
  --max-instances 1 \
  --set-env-vars VERTEX_PROJECT_ID=<id>,VERTEX_DATA_STORE_ID=<id>,VERTEX_ENGINE_ID=<id>,MODEL_PROVIDER=google_genai,SESSION_STORE_BACKEND=firestore,FIRESTORE_COLLECTION=sessions \
  --set-secrets APP_PASSWORD=<secret>:latest,JWT_SECRET_KEY=<secret>:latest
```

- **`--set-env-vars` replaces the entire set (it does not merge)**: on redeploy, any variable omitted from the list is deleted (e.g. dropping `SESSION_STORE_BACKEND=firestore` silently reverts conversations to the in-memory store and wipes them on deploy). When updating code only, omit env flags, or use `--update-env-vars` to merge.
- **`--max-instances 1`**: the `memory` backend keeps state in memory, so multiple instances would be inconsistent; the `firestore` backend supports multiple instances.

> Note: before an actual deploy, you must restore the stubbed client-construction logic in `services/client_factory.py` / `services/model_factory.py` (see the showcase-build notice at the top of this README).
