FROM python:3.11-slim

WORKDIR /app

RUN useradd -r -u 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

USER appuser

EXPOSE 5003

CMD ["python", "server.py"]
