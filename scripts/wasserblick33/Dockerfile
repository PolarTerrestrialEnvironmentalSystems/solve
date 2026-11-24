FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-lock.txt /app/requirements-lock.txt
RUN pip install --no-cache-dir -r /app/requirements-lock.txt
# Explicit COPY patterns intentionally exclude .env and downloaded datasets.
COPY *.py *.json *.sql /app/
RUN useradd --create-home --uid 10001 solve
USER solve
CMD ["python", "pg_import.py", "--help"]
