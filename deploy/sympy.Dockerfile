# Build the pinned runtime independently; no package downloads at execution.
# Each receipt pins the built image's immutable ID. No host directories mounted.
FROM python:3.13-slim
RUN python -m venv /app/.venv && /app/.venv/bin/pip install --no-cache-dir sympy==1.14.0
USER 65534:65534
ENTRYPOINT ["/app/.venv/bin/python", "-I", "-u"]
