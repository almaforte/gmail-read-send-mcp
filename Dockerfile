FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gmail_send_mcp.py .
COPY corsalis_logo.b64 .

ENV PORT=3001
EXPOSE 3001

CMD ["python", "gmail_send_mcp.py"]
