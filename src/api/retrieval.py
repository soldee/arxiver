import logging
import json
from sentence_transformers import SentenceTransformer

from src.core.config import cfg
from src.etl.ingestor.fetcher import Paper

class Retriever:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.model = SentenceTransformer(cfg.EMBEDDING_MODEL_NAME, device=cfg.api.PYTORCH_DEVICE, local_files_only=True)
    
    def retrieve_and_rank(self, conn, query):
        if not query or query == "":
            self.logger.error("Query is empty")
            return

        embeddings = self.model.encode(query, normalize_embeddings=True)
        embeddings_str = json.dumps(embeddings.tolist())

        with conn.cursor() as cur:
            cur.execute(
                f"""
                    SELECT id, datestamp, title, abstract
                    FROM {cfg.POSTGRES_ARXIV_TABLE}
                    ORDER BY embedding <=> %s::vector LIMIT 20
                """, (embeddings_str,)
            )
            conn.commit()
            papers: list[Paper] = [Paper(x[0], x[1], x[2], x[3]) for x in cur.fetchall()]

        return papers
