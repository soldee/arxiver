import argparse

def ingest(set_name: str, quantity: int):
    from src.core.config import cfg
    from src.etl.ingestor.fetcher import ArxivOaiFetcher, Paper, Resumables
    from src.etl.ingestor.embeddings import EmbeddingsGen
    from src.etl.ingestor.repository import PaperRepository

    import logging
    import psycopg2

    logging.basicConfig(
        level=cfg.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    logger = logging.getLogger(__name__)

    with psycopg2.connect(cfg.DB_URL) as conn:
        resumables = Resumables(conn)
        fetcher = ArxivOaiFetcher(conn, resumables)
        gen = EmbeddingsGen(cfg.MODEL_BATCH_SIZE)
        repo = PaperRepository(conn)

        count = 0

        while count < quantity:
            papers: list[Paper] = fetcher.request_batch(set_name)

            if len(papers) == 0:
                if count > 0:
                    logger.info("No more paper's returned by arXiv, might have hit end of stream. Commiting last resumables.")
                    resumables.commit(set_name)
                else:
                    logger.error("No papers found for ListSet: %s", set_name)
                return

            for chunk, emb in gen.generate_embeddings(papers):
                repo.insert_batch_with_embeddings(zip(chunk, emb.tolist()))

            resumables.commit(set_name)

            count += len(papers)
            logger.info("Total ingested count: %d/%d", count, quantity)


if __name__ == '__main__':
    parser = argparse.ArgumentParser("Arxivers")
    parser.add_argument(
        'list_set', type=str,
        help="Specific category for selective harvesting. See https://oaipmh.arxiv.org/oai?verb=ListSets. E.g. 'cs:cs:RO' to harvest robotics papers"
    )
    parser.add_argument('quantity', type=int, help="Number of papers to ingest")
    args = parser.parse_args()
    
    set_name = args.list_set
    quantity = args .quantity

    ingest(set_name, quantity)
