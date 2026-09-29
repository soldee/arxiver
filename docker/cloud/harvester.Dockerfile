FROM python:3.12.14-trixie

WORKDIR /app

COPY docker/cloud/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

CMD ["python", "-m", "src.cloud.harvester"]
