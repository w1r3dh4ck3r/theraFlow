# =============================================================
# Multi-stage Dockerfile for TheraFlow
# Stage 1 – builder: install dependencies + package with uv
# Stage 2 – runtime: lean image with only what's needed
# =============================================================

# ---------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------
FROM python:3.13-slim AS builder

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy everything needed for install
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Create venv, install deps + package in one step
RUN uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python .

# ---------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------
FROM python:3.13-slim AS runtime

# Non-root user for security
RUN addgroup --system theraflow && adduser --system --ingroup theraflow theraflow

WORKDIR /app

# Copy the pre-built virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Make venv binaries take priority on PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER theraflow

EXPOSE 8000

CMD ["uvicorn", "theraflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
