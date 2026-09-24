import logging
import json
from sentence_transformers import SentenceTransformer
import asyncio
import time
from abc import ABC, abstractmethod
from typing import List, Tuple

from src.core.config import cfg
from src.etl.ingestor.fetcher import Paper


class EmbeddingGen(ABC):
    def __init__(self, model_name: str, model_device: str):
        self.logger = logging.getLogger(__name__)
        self.model = SentenceTransformer(model_name, device=model_device, local_files_only=True)

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        pass

class SimpleEmbeddingGen(EmbeddingGen):
    async def embed(self, text: str) -> List[float]:
        embeddings = await asyncio.to_thread(self.model.encode, text, normalize_embeddings=True)
        return embeddings.tolist()

class BatchedEmbeddingGen(EmbeddingGen):
    def __init__(self, model_name: str, model_device: str):
        super().__init__(model_name, model_device)
        self.queue = asyncio.Queue()
        self.task: asyncio.Task | None = None

    async def start_worker(self, max_latency: int, max_batch_size: int):
        if self.task is None:
            self.task = asyncio.create_task(self._worker(max_latency, max_batch_size))

    async def stop_worker(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            self.logger.info("batched embeddings worker stopped")

    async def _worker(self, max_latency: int, max_batch_size: int):
        texts: list[str] = []
        futures: list[asyncio.Future] = []

        start_time = time.monotonic()

        while True:
            while len(texts) < max_batch_size:
                elapsed = time.monotonic() - start_time
                remaining_time = max_latency - elapsed

                if remaining_time <= 0:
                    break

                try:
                    text, future = await asyncio.wait_for(self.queue.get(), timeout=remaining_time)
                    texts.append(text)
                    futures.append(future)
                except asyncio.TimeoutError:
                    break

            start_time = time.monotonic()

            if len(texts) != 0:
                try:
                    embeddings = await asyncio.to_thread(self.model.encode, texts, normalize_embeddings=True)
                    for fut, emb in zip(futures, embeddings.tolist()):
                        if not fut.done():
                            fut.set_result(emb)
                except Exception as e:
                    for fut in futures:
                        if not fut.done():
                            fut.set_exception(e)
                finally:
                    for _ in range(len(futures)):
                        self.queue.task_done()
                    texts.clear()
                    futures.clear()

    async def embed(self, text: str) -> List[float]:
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put((text, fut))
        return await fut    


class Retriever:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    async def retrieve_and_rank(self, conn, embedding: list[float]) -> list[Paper]:
        if not embedding or len(embedding) == 0:
            self.logger.error("Received empty embeddings")
            return []

        embeddings_str = json.dumps(embedding)

        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                    SELECT id, datestamp, title, abstract
                    FROM {cfg.POSTGRES_ARXIV_TABLE}
                    ORDER BY embedding <=> %s::vector LIMIT 20
                """, (embeddings_str,)
            )
            await conn.commit()
            papers: list[Paper] = [Paper(x[0], x[1], x[2], x[3]) for x in await cur.fetchall()]

        return papers

