# SwingTrader backend image.
#
# Build context is the repo root (not backend/), because the app imports as
# `backend.app.*` and needs `requirements.txt` from the root:
#
#   docker build -f backend/Dockerfile -t swingtrader-backend .
#
FROM python:3.12-slim

WORKDIR /srv

# System deps for pyarrow/pandas wheels are covered by the slim image's
# manylinux wheel compatibility; no extra apt packages are needed as of the
# pinned requirements.txt versions.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
