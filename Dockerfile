FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping nslookup dnsutils && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python -m compileall app tests alembic

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
