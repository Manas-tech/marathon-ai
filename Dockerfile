FROM python:3.12-slim

# poppler-utils: required by pdf2image to rasterize uploaded PDF pages.
# No other system deps needed -- ezdxf is pure Python, matplotlib runs
# headless via the Agg backend already set in app/services/dxf_service.py.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY alembic ./alembic
COPY alembic.ini .

# Render sets $PORT at runtime; the app must bind to it, not a fixed port.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
