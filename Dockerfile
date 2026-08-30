FROM python:3.12-slim

WORKDIR /app

# git e' necessario a pip per installare gmail_message_rules direttamente
# da GitHub (vedi requirements.txt): python:3.12-slim non lo include di
# default.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gmail_send_mcp.py .
COPY corsalis_logo.b64 .

ENV PORT=3001
EXPOSE 3001

CMD ["python", "gmail_send_mcp.py"]
