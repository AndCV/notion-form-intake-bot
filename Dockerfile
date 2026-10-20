# Web app image (Render; see README, "Deploying the web app").
FROM python:3.12-slim

# System libraries WeasyPrint needs to render the invoice/receipt PDFs
# (same set as .github/workflows/poll.yml), plus a metric-compatible
# Arial/Helvetica substitute for the templates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libffi-dev shared-mime-info fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY lib ./lib
COPY templates ./templates
COPY db ./db
COPY scripts ./scripts

# The host sets $PORT. --proxy-headers so request.client is the visitor,
# not Google's front end.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips="*"
