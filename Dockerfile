FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY logai/ ./logai/
COPY static/ ./static/
COPY tools/ ./tools/
COPY run.py .

ENV SYSLOG_UDP_PORT=5514 SYSLOG_TCP_PORT=5514 API_PORT=8080 DB_PATH=/data/logai.db
VOLUME ["/data"]
EXPOSE 8080 5514/udp 5514/tcp

CMD ["python", "run.py"]
