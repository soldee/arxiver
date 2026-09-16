from config import cfg
from ingestion import (
    Ingestor,
    Paper
)

import logging
import psycopg2

if __name__ == '__main__':
    logging.basicConfig(
        level=cfg.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    conn = psycopg2.connect(cfg.DB_URL)

    ingestor = Ingestor(conn)

    papers: list[Paper] = ingestor.request_batch()
    if len(papers) != 0:
        with open('papers.txt', 'w+') as f:
            for paper in papers:
                f.write(f"{paper.datestamp}, {paper.title}, {paper.abstract}\n")
