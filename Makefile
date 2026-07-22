PYTHON ?= $(if $(wildcard .venv/bin/python3),.venv/bin/python3,python3)
PY_FILES := $(shell find . -name '*.py' -not -path './.venv/*' -not -path './.git/*')
FASTAPI_HOST ?= 0.0.0.0
FASTAPI_PORT ?= 8080

# Auto-load local env vars when .env exists.
# Safe fallback: if .env is absent, Make continues normally.
ifneq (,$(wildcard .env))
include .env
export VERTEX_PROJECT_ID
export VERTEX_DATA_STORE_ID
export VERTEX_LOCATION
export VERTEX_INIT_LOCATION
export VERTEX_ENGINE_ID
ifneq ($(strip $(GOOGLE_CLOUD_PROJECT)),)
export GOOGLE_CLOUD_PROJECT
endif
ifneq ($(strip $(GOOGLE_APPLICATION_CREDENTIALS)),)
export GOOGLE_APPLICATION_CREDENTIALS
endif
export APP_PASSWORD
export ADMIN_PASSWORD
export GCS_STAGING_BUCKET
endif

.PHONY: help install install-dev test lint run check eval

help:
	@echo "Available targets:"
	@echo "  make install                 # install FastAPI runtime dependencies"
	@echo "  make install-dev             # install runtime + dev dependencies"
	@echo "  make lint                    # lightweight syntax checks"
	@echo "  make test                    # run pytest"
	@echo "  make check                   # run lint + test"
	@echo "  make eval                    # run end-to-end eval cases"
	@echo "  make run                     # run FastAPI app (port 8080)"
	@echo "                               # (auto-loads .env when present)"

install:
	$(PYTHON) -m pip install -r requirements-api.txt

install-dev: install
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m py_compile $(PY_FILES)

test:
	$(PYTHON) -m pytest -q

check: lint test

eval:
	$(PYTHON) scripts/run_eval.py --cases eval/eval_cases.json --output eval/eval_report.json

run:
	$(PYTHON) -m uvicorn api.main:app --reload --host $(FASTAPI_HOST) --port $(FASTAPI_PORT)

