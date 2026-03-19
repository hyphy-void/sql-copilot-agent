# Repository Guidelines

## Project Structure & Module Organization
- `backend/`: FastAPI application and core SQL copilot logic (`main.py`, parser, schema manager, DDL guard/planner, LLM adapter).
- `agent/`: LangGraph workflow orchestration (`graph.py`).
- `frontend/`: Static UI assets served by FastAPI (`index.html`, `monaco.js`).
- `tests/`: `pytest` suite for API, parser, engine, guard, and workflow behavior.
- `db/`: local SQLite artifacts and schema bootstrap (`init.sql`, runtime `*.db` files).
- `scripts/`: small utility scripts (for example `scripts/demo.sh` for endpoint smoke tests).

## Build, Test, and Development Commands
- `uv sync --dev`: install runtime and test dependencies into the local `.venv`.
- `cp .env.example .env`: create local configuration template before running.
- `uv run uvicorn backend.main:app --reload`: start API and static frontend at `http://127.0.0.1:8000`.
- `uv run pytest -q`: run all tests in quiet mode.
- `bash scripts/demo.sh`: quick manual check of `/health`, `/schema/tables`, and `/autocomplete`.

## Coding Style & Naming Conventions
- Follow Python 3.11+ conventions: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- Keep type hints on public interfaces and request/response models (consistent with `backend/models.py` usage).
- Prefer small focused modules under `backend/`; avoid mixing API routing and business logic.
- Frontend files are plain static assets; keep JavaScript modular and API paths explicit.

## Testing Guidelines
- Framework: `pytest` with FastAPI `TestClient` fixtures from `tests/conftest.py`.
- Name tests as `tests/test_<feature>.py`; name cases as `test_<behavior>()`.
- For backend changes, add or update tests in the closest domain file (parser, autocomplete, chat API, DDL, graph).
- No enforced coverage gate currently; treat meaningful regression coverage as required for new behavior.

## Commit & Pull Request Guidelines
- Use Conventional Commit style seen in history, e.g. `feat: ...`, `fix: ...`, `chore: ...`.
- Keep each commit scoped to one logical change and include related tests.
- PRs should include: purpose, key implementation notes, test evidence (`uv run pytest -q` output summary), and screenshots/GIFs for UI updates.
- Never commit secrets; keep credentials only in local `.env` (already ignored).
