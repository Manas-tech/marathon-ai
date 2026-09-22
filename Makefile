.PHONY: help venv install run dev migrate migrate-auto downgrade db-reset clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
ALEMBIC := $(VENV)/bin/alembic

help:
	@echo "Targets:"
	@echo "  make venv          create the virtualenv (.venv)"
	@echo "  make install       install Python deps into .venv"
	@echo "  make migrate       apply all pending Alembic migrations (alembic upgrade head)"
	@echo "  make migrate-auto  autogenerate a new migration from model changes"
	@echo "  make downgrade     roll back one migration (alembic downgrade -1)"
	@echo "  make run           run the API with uvicorn (production-ish, no reload)"
	@echo "  make dev           run the API with uvicorn --reload for local development"
	@echo "  make db-reset      drop the local sqlite DB and re-run migrations from scratch"
	@echo "  make clean         remove .venv, __pycache__, and the local sqlite DB"

venv:
	python3 -m venv $(VENV)

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

migrate:
	$(ALEMBIC) upgrade head

migrate-auto:
	$(ALEMBIC) revision --autogenerate -m "$(m)"

downgrade:
	$(ALEMBIC) downgrade -1

run:
	$(PY) startup.py

dev:
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

db-reset:
	rm -f drawing_validator.db
	$(ALEMBIC) upgrade head

clean:
	rm -rf $(VENV) drawing_validator.db uploads
	find . -type d -name "__pycache__" -exec rm -rf {} +
