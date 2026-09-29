.PHONY: help setup dev api web install install-web test lint fmt verify db-reset recovery-doctor recovery-backup recovery-restore

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "make setup        - one-shot: install api+web deps and bootstrap .env"
	@echo "make install      - create venv + install api deps (dev extras)"
	@echo "make install-web  - npm install in /web"
	@echo "make dev          - run api (uvicorn) + web (vite) with hot reload"
	@echo "make api          - run api only"
	@echo "make web          - run web only"
	@echo "make test         - run pytest (offline, fixtures)"
	@echo "make lint         - ruff check"
	@echo "make fmt          - ruff format"
	@echo "make verify LEAGUE=<id> [SEASON=2026] [LABEL=main] [CROSSCHECK=1]  - live smoke test"
	@echo "make recovery-doctor - inspect local recovery readiness (secret-free)"
	@echo "make recovery-backup - run an operator-approved manual recovery point"
	@echo "make recovery-restore - run the operator-approved break-glass restore drill"

# Fresh-clone-to-running: `make setup` then `make dev`.
setup: install install-web
	@if [ ! -f .env ]; then \
	  cp .env.example .env; \
	  KEY=$$($(PY) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"); \
	  $(PY) -c "import pathlib,re,sys; p=pathlib.Path('.env'); p.write_text(re.sub(r'^FERNET_KEY=.*', 'FERNET_KEY='+sys.argv[1], p.read_text(), flags=re.M))" "$$KEY"; \
	  echo "Wrote .env with a generated FERNET_KEY."; \
	else echo ".env already exists — leaving it untouched."; fi
	@echo "Setup complete. Run 'make dev' to start."

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

# Cookies for private leagues come from ESPN_SWID/ESPN_S2 in .env (or a hidden
# prompt) — never passed as args. LABEL names the stored account; CROSSCHECK=1
# also diffs against espn-api.
verify:
	$(PY) -m api.verify --league $(LEAGUE) $(if $(SEASON),--season $(SEASON),) $(if $(LABEL),--label $(LABEL),) $(if $(CROSSCHECK),--cross-check,)

db-reset:
	rm -f data/edge.db data/edge.db-* && echo "dropped data/edge.db"

recovery-doctor:
	$(PY) -m api.recovery doctor --json

recovery-backup:
	$(PY) -m api.recovery backup --reason manual

recovery-restore:
	$(PY) -m api.recovery restore-drill --snapshot latest --json
