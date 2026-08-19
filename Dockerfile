FROM python:3.11-slim

WORKDIR /app

# Install Docker CLI (needed to talk to Docker daemon via socket)
RUN apt-get update && \
    apt-get install -y --no-install-recommends docker.io && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data

CMD ["python", "bot.py"]
