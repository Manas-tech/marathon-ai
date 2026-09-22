# Drawing Validator API

FastAPI conversion of the original Streamlit "Drawing Validator" app. Same
pipeline (render PDF → Gemini vision extraction → cross-reference
comparison → report), now exposed as an HTTP API with a small static
frontend, persisted job state (SQLite/Postgres via SQLAlchemy), and Alembic
migrations.

## Project layout

```
app/
  main.py            FastAPI app factory / entrypoint
  core/               settings (config.py) and logging setup
  db/                 SQLAlchemy engine/session (session.py), declarative
                       base (base_class.py), model registry for Alembic (base.py)
  middleware/          CORS, request logging
  models/               Project/ProjectDrawing/ExtractionResult/ComparisonResult ORM models
  routers/             health, comparisons (the API), frontend (serves the UI)
  schemas/              drawing.py = the original extraction/comparison
                        Pydantic schemas; job.py = API request/response models
  services/             pdf_service, extraction_service, comparison_service,
                        report_service, usage_service -- ported 1:1 from the
                        original pdf_utils.py / extractor.py /
                        matcher_comparator.py / report.py / usage_tracker.py.
                        pipeline_service.py is the new orchestrator that runs
                        the three steps and writes progress to the DB.
static/
  templates/index.html  browser UI (replaces the old Streamlit page)
  css/, js/              styling + fetch/poll logic
alembic/                 migrations (env.py reads DATABASE_URL from app settings)
startup.py                `python startup.py` launcher
Makefile                  `make install`, `make dev`, `make migrate`, ...
```

## Setup

```bash
cp .env.example .env
# edit .env and set GEMINI_API_KEY

make install
make migrate     # applies alembic/versions/*.py, creates the project/drawing tables
make dev         # http://localhost:8000  (auto-reload)
# or: make run   (no reload, what startup.py does)
```

`packages.txt` lists the one OS-level dependency: `poppler-utils` (required
by `pdf2image` to rasterize PDF pages). Install it via your package manager,
e.g. `apt-get install poppler-utils` on Debian/Ubuntu, before running the app.

By default `AUTO_CREATE_TABLES=true` in `.env.example` also creates tables
on startup via `Base.metadata.create_all()`, purely as a local-dev
convenience so `make dev` works even if you forget `make migrate`. Set it to
`false` and rely on Alembic migrations for anything beyond local dev.

## API

| Method | Path                                  | Purpose                                   |
|--------|----------------------------------------|--------------------------------------------|
| POST   | `/api/v1/comparisons`                  | Upload a GAD + sub-drawing PDFs, starts a job |
| GET    | `/api/v1/comparisons`                  | List recent jobs                          |
| GET    | `/api/v1/comparisons/{id}`             | Poll job status / fetch the result        |
| GET    | `/api/v1/comparisons/{id}/report.md`   | Download the Markdown report              |
| GET    | `/api/v1/comparisons/{id}/report.json` | Download the raw JSON report              |
| GET    | `/api/v1/health`                       | Health check                              |

Interactive docs at `/docs` (Swagger UI) and `/redoc` once the app is running.

Processing runs as a background task: `POST /comparisons` returns
immediately with `{"id": ..., "status": "PENDING"}`; poll
`GET /comparisons/{id}` until `status` is `COMPLETE` or `FAILED`. The bundled
frontend (`/`) does this polling for you.

## Database / migrations

Any SQLAlchemy URL works via `DATABASE_URL` in `.env` -- defaults to a local
SQLite file so the app runs with zero extra setup. To use Postgres instead:

```
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/drawing_validator
```

(install `psycopg2-binary` if you switch to Postgres -- it isn't in
`requirements.txt` by default to keep the local/dev path dependency-free).

Common migration commands (also available as `make` targets):

```bash
alembic upgrade head                          # apply migrations
alembic revision --autogenerate -m "message"   # generate a new migration after model changes
alembic downgrade -1                           # roll back one migration
```

## Notes on what changed vs. the original scripts

- `app.py` (Streamlit UI) → replaced by `static/templates/index.html` +
  `static/js/app.js`, served by `app/routers/frontend.py`.
- `main.py` (CLI) → superseded by the API; the same three-step pipeline now
  lives in `app/services/pipeline_service.py` and is invoked over HTTP
  instead of argparse. If you still want a CLI, it's straightforward to add
  one that calls the same service functions directly.
- `extractor.py`, `matcher_comparator.py`, `pdf_utils.py`, `report.py`,
  `usage_tracker.py`, `schemas.py` → moved under `app/services/` and
  `app/schemas/` with no logic changes, just reading config from
  `app.core.config.get_settings()` instead of `os.environ` directly.
- `st.session_state` (lost on refresh) → replaced by a synchronous API call
  that returns the finished report directly; extraction status persists
  per-drawing on `project_drawings.is_extracted` instead.
