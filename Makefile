SHELL := /bin/bash
PYTHON ?= .venv/bin/python

.PHONY: bootstrap doctor dev dev-auto dev-docker dev-local test render-sample qf-smoke stop

bootstrap:
	bash scripts/bootstrap.sh

doctor:
	$(PYTHON) scripts/doctor.py --local

dev:
	$(MAKE) dev-local

dev-auto:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		$(MAKE) dev-docker; \
	else \
		echo "Docker is not accessible for this user; starting local dev servers instead."; \
		echo "To use containers later, fix Docker socket access or run make dev-docker from a Docker-enabled shell."; \
		$(MAKE) dev-local; \
	fi

dev-docker:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "Docker is not installed or is not on PATH."; \
		exit 1; \
	fi
	@if ! docker info >/dev/null 2>&1; then \
		echo "Docker is installed but this user cannot access the Docker daemon."; \
		echo "Check that Docker is running and that your user can access /var/run/docker.sock."; \
		exit 1; \
	fi
	docker compose up --build

dev-local:
	@set -euo pipefail; \
	API_HOST=$${API_HOST:-127.0.0.1}; \
	API_PORT=$${API_PORT:-8000}; \
	WEB_HOST=$${WEB_HOST:-127.0.0.1}; \
	WEB_PORT=$${WEB_PORT:-3000}; \
	API_PROXY_TARGET=$${API_PROXY_TARGET:-http://$${API_HOST}:$${API_PORT}}; \
	cleanup() { \
		if [ -n "$${API_PID:-}" ]; then kill "$$API_PID" 2>/dev/null || true; fi; \
		if [ -n "$${WEB_PID:-}" ]; then kill "$$WEB_PID" 2>/dev/null || true; fi; \
		wait 2>/dev/null || true; \
	}; \
	trap cleanup INT TERM EXIT; \
	echo "API: http://$${API_HOST}:$${API_PORT}"; \
	echo "Web: http://$${WEB_HOST}:$${WEB_PORT}"; \
	API_ORIGIN=$${API_ORIGIN:-http://$${API_HOST}:$${API_PORT}} \
	WEB_ORIGIN=$${WEB_ORIGIN:-http://$${WEB_HOST}:$${WEB_PORT}} \
	REDIS_URL=$${REDIS_URL:-redis://127.0.0.1:6379/0} \
	SQLITE_PATH=$${SQLITE_PATH:-data/cache/local/jobs.sqlite3} \
	$(PYTHON) -m uvicorn quran_video_api.main:app --host "$$API_HOST" --port "$$API_PORT" & \
	API_PID=$$!; \
	API_PROXY_TARGET="$$API_PROXY_TARGET" npm run dev --prefix apps/web -- --host "$$WEB_HOST" --port "$$WEB_PORT" & \
	WEB_PID=$$!; \
	wait -n "$$API_PID" "$$WEB_PID"; \
	EXIT_CODE=$$?; \
	cleanup; \
	exit "$$EXIT_CODE"

test:
	$(PYTHON) -m compileall apps packages worker scripts
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/mypy apps packages worker scripts
	.venv/bin/pytest -q
	npm ci --prefix apps/web
	npm run lint --prefix apps/web
	npm run typecheck --prefix apps/web
	npm run test --prefix apps/web
	npm run build --prefix apps/web
	docker compose config
	$(PYTHON) scripts/validate_workflow.py
	$(PYTHON) scripts/doctor.py --local
	$(PYTHON) scripts/render_sample.py

render-sample:
	$(PYTHON) scripts/render_sample.py

qf-smoke:
	$(PYTHON) scripts/qf_smoke.py

stop:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		docker compose down; \
	else \
		echo "Docker is not accessible; no Docker compose stack was stopped."; \
	fi
