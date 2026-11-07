FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TS_SMS_LEDGER_PATH=/var/lib/ts-sms/ledger.jsonl

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY ts_sms ./ts_sms
RUN pip install . \
    && useradd --system --uid 10001 --home-dir /app ts-sms \
    && mkdir -p /var/lib/ts-sms \
    && chown ts-sms /var/lib/ts-sms

USER ts-sms
VOLUME ["/var/lib/ts-sms"]

CMD ["ts-sms-worker"]
