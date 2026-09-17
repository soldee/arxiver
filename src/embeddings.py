from config import cfg
from sentence_transformers import SentenceTransformer
from fetcher import Paper

class EmbeddingsGen:

    def __init__(self, batch_size: int):
        self.model = SentenceTransformer(cfg.EMBEDDING_MODEL_NAME, device='cuda', local_files_only=True)
        self.batch_size = batch_size

    def generate_embeddings(self, papers: list[Paper]):
        i = 0
        n = len(papers)

        for i in range(0, n, self.batch_size):
            chunk = papers[i:i+self.batch_size]
            emb = self.model.encode([x.abstract for x in chunk], batch_size=self.batch_size)
            yield (chunk, emb)
