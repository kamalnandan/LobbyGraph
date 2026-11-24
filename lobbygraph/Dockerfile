# LobbyGraph loader image (Azure Container Apps Job)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config ./config
COPY queries ./queries
COPY src ./src
# Source files: GLEIF cache, plus (PoC mode) the register export and the two Commission meeting files.
COPY data/raw ./data/raw
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "lobbygraph.job"]
