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

    logger = logging.getLogger(__name__)

    with psycopg2.connect(cfg.DB_URL) as conn:
        fetcher = ArxivOaiFetcher(conn)
        gen = EmbeddingsGen(cfg.MODEL_BATCH_SIZE)
        repo = PaperRepository(conn)

        count = 0

        while count < cfg.TOTAL_INGESTION_PAPER_NUM:
            papers: list[Paper] = fetcher.request_batch()

            for chunk, emb in gen.generate_embeddings(papers):
                repo.insert_batch_with_embeddings(zip(chunk, emb.tolist()))

            fetcher.update_resumables()

            count += len(papers)
            logger.info("Total ingested count: %d/%d", count, cfg.TOTAL_INGESTION_PAPER_NUM)
