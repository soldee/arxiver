from config import cfg
from fetcher import (
    ArxivOaiFetcher,
    Paper
)
from embeddings import EmbeddingsGen
from repository import PaperRepository

import logging
import psycopg2

if __name__ == '__main__':
    logging.basicConfig(
        level=cfg.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    conn = psycopg2.connect(cfg.DB_URL)
    fetcher = ArxivOaiFetcher(conn)
    gen = EmbeddingsGen(cfg.MODEL_BATCH_SIZE)

    papers: list[Paper] = fetcher.request_batch()

    repo = PaperRepository(conn)

    for chunk, emb in gen.generate_embeddings(papers):
        repo.insert_batch_with_embeddings(zip(chunk, emb.tolist()))

    fetcher.update_resumables()
