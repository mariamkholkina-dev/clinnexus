FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml /app/pyproject.toml
COPY . /app

RUN pip install --upgrade pip && \
    pip install fastapi[all] uvicorn[standard] psycopg[binary,pool] sqlalchemy[asyncio] alembic pydantic[email] pgvector pydantic-settings && \
    pip install black ruff mypy pytest httpx

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


