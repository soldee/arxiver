import logging
import argparse
import json
import psycopg2
from sentence_transformers import SentenceTransformer

from src.core.config import cfg
from src.etl.ingestor.fetcher import Paper

class Retriever:
    def __init__(self, conn):
        self.logger = logging.getLogger(__name__)
        self.conn = conn
        self.model = SentenceTransformer(cfg.EMBEDDING_MODEL_NAME, device=cfg.etl.PYTORCH_DEVICE, local_files_only=True)
    
    def retrieve_and_rank(self, query):
        if not query or query == "":
            self.logger.error("Query can't be empty")
            return

        embeddings = self.model.encode(query, normalize_embeddings=True)
        embeddings_str = json.dumps(embeddings.tolist())

        with self.conn.cursor() as cur:
            cur.execute(
                """
                    SELECT id, datestamp, title, abstract
                    FROM arxiv_meta
                    ORDER BY embedding <=> %s::vector LIMIT 20
                """, (embeddings_str,)
            )
            self.conn.commit()
            papers: list[Paper] = [Paper(x[0], x[1], x[2], x[3]) for x in cur.fetchall()]

        return papers


if __name__ == '__main__':

    parser = argparse.ArgumentParser("query")
    parser.add_argument('query', type=str, help="Natural language query")
    args = parser.parse_args()

    query = args.query

    with psycopg2.connect(cfg.DB_URL) as conn:
        papers = Retriever(conn).retrieve_and_rank(query)
        for x in papers:
            print(f"{x}\n")