.PHONY: help dev api web install install-web test lint fmt verify db-reset

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "make install      - create venv + install api deps (dev extras)"
	@echo "make install-web  - npm install in /web"
	@echo "make dev          - run api (uvicorn) + web (vite) with hot reload"
	@echo "make api          - run api only"
	@echo "make web          - run web only"
	@echo "make test         - run pytest (offline, fixtures)"
	@echo "make lint         - ruff check"
	@echo "make fmt          - ruff format"
	@echo "make verify LEAGUE=<id> [SEASON=2026] [ACCOUNT=<id>]  - live smoke test"

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

install-web:
	cd web && npm install

# Run both dev servers with hot reload. Ctrl-C stops both.
dev:
	@echo "Starting api on :8000 and web on :5173 (Ctrl-C to stop both)"
	@trap 'kill 0' INT TERM; \
	$(PY) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000 & \
	(cd web && npm run dev) & \
	wait

api:
	$(PY) -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

web:
	cd web && npm run dev

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check api tests

fmt:
	$(PY) -m ruff format api tests

verify:
	$(PY) -m api.verify --league $(LEAGUE) $(if $(SEASON),--season $(SEASON),) $(if $(ACCOUNT),--account $(ACCOUNT),)

db-reset:
	rm -f data/edge.db data/edge.db-* && echo "dropped data/edge.db"
