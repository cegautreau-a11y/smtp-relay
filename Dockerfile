FROM python:3.12-slim

WORKDIR /app

# No compiler needed: every dependency ships cp312 manylinux wheels
# (psycopg2-BINARY, bcrypt, cryptography, …), so the ~200 MB gcc
# toolchain would sit in the final image unused.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. /app/data is the persistent volume mount point
# (an optional LOG_FILE lives there; the database is external PostgreSQL —
# see docker-compose.yml).
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8025 2525

ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
