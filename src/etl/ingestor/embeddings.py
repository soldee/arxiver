from src.core.config import cfg
from src.etl.ingestor.fetcher import Paper

import logging
import torch
import gc
from sentence_transformers import SentenceTransformer

class EmbeddingsGen:
    def __init__(self, batch_size: int):
        self.model = SentenceTransformer(cfg.EMBEDDING_MODEL_NAME, device=cfg.etl.PYTORCH_DEVICE, local_files_only=True)
        self.batch_size = batch_size
        self.logger = logging.getLogger(__name__)

    def _encode_with_retry(self, texts: list[str], initial_batch_size: int):
        current_batch_size = initial_batch_size
        
        while current_batch_size >= 1:
            oom_triggered = False
            
            try:
                return self.model.encode(texts, batch_size=current_batch_size, normalize_embeddings=True)
            except torch.cuda.OutOfMemoryError:
                oom_triggered = True
            
            # reclaim memory after exception context and traceback are destroyed
            if oom_triggered:
                gc.collect()
                torch.cuda.empty_cache()
                
                if current_batch_size <= 1:
                    raise RuntimeError("OOM even with batch_size=1. An abstract is too long.")
                
                current_batch_size = current_batch_size // 2
                self.logger.warning("GPU OOM caught. Cleared cache. Retrying with batch_size=%d", current_batch_size)
                
    def generate_embeddings(self, papers: list[Paper]):
        n = len(papers)

        for i in range(0, n, self.batch_size):
            chunk = papers[i:i+self.batch_size]
            texts = [x.abstract for x in chunk]
            
            emb = self._encode_with_retry(texts, self.batch_size)
            yield (chunk, emb)
