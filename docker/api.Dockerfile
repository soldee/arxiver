FROM python:3.12.14-trixie

WORKDIR /app

# install pytorch independently from requirements. The only way to get it working correctly
RUN pip install torch --index-url https://download.pytorch.org/whl/cu132
COPY requirements.txt .
RUN grep -vE '^(torch|nvidia-|cuda-|triton)' requirements.txt | pip install --no-cache-dir -r /dev/stdin

ARG EMBEDDING_MODEL_NAME
ENV EMBEDDING_MODEL_NAME=${EMBEDDING_MODEL_NAME}
ARG API__RERANKER_MODEL_NAME
ENV API__RERANKER_MODEL_NAME=${API__RERANKER_MODEL_NAME}

# pre-download the sentence transformers models
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBEDDING_MODEL_NAME'])"
RUN python -c "import os; from sentence_transformers import CrossEncoder; CrossEncoder(os.environ['API__RERANKER_MODEL_NAME'])"

COPY src ./src

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
