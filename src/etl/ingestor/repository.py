from src.etl.ingestor.fetcher import Paper

import logging

class PaperRepository:
    def __init__(self, conn):
        self.conn = conn
        self.logger = logging.getLogger(__name__)
    
    def insert_batch_with_embeddings(self, papers_with_embeddings: list[tuple[Paper, list[float]]]):
        if not papers_with_embeddings:
            self.logger.warning("Received 0 embeddings to insert")
            return

        query = """
            INSERT INTO arxiv_meta (id, datestamp, title, abstract, embedding)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
            datestamp = excluded.datestamp,
            title = excluded.title,
            abstract = excluded.abstract,
            embedding = excluded.embedding;
        """
        
        data = [
            (paper.id, paper.datestamp, paper.title, paper.abstract, embedding)
            for paper, embedding in papers_with_embeddings
        ]

        with self.conn.cursor() as cur:
            cur.executemany(query, data)
        self.conn.commit()