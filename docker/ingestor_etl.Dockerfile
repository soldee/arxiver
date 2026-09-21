FROM python:3.12.14-trixie

WORKDIR /etl

ARG EMBEDDING_MODEL_NAME
ENV EMBEDDING_MODEL_NAME=${EMBEDDING_MODEL_NAME}

# install pytorch independently from requirements. The only way to get it working correctly
RUN pip install torch --index-url https://download.pytorch.org/whl/cu132
COPY requirements.txt .
RUN grep -vE '^(torch|nvidia-|cuda-|triton)' requirements.txt | pip install --no-cache-dir -r /dev/stdin

# pre-download the sentence transformers model
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBEDDING_MODEL_NAME'])"

COPY src ./src

CMD ["python", "-m", "src.etl.ingestor.runner", "-h"]
