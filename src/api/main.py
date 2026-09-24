from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from pydantic import BaseModel
from psycopg_pool import AsyncConnectionPool
import logging

from src.api.retrieval import EmbeddingGen, SimpleEmbeddingGen, BatchedEmbeddingGen, Retriever
from src.core.config import cfg


logging.basicConfig(
    level=cfg.etl.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing PostgreSQL Connection Pool.")
    app.state.db_pool = AsyncConnectionPool(
        conninfo=cfg.DB_URL,
        min_size=cfg.api.MIN_DB_CONNECTIONS,
        max_size=cfg.api.MAX_DB_CONNECTIONS,
        open=False
    )
    await app.state.db_pool.open()

    logger.info("Initializing Retriever for inference.")
    retriever = Retriever()
    app.state.retriever = retriever

    embedding_gen: EmbeddingGen
    if cfg.api.ENABLE_EMBEDDING_BATCHING:
        logger.info("Initializing BatchedEmbeddingGen")
        embedding_gen = BatchedEmbeddingGen(cfg.EMBEDDING_MODEL_NAME, cfg.api.PYTORCH_DEVICE)
        await embedding_gen.start_worker(cfg.api.EMBEDDING_MAX_LATENCY_S, cfg.api.EMBEDDING_MAX_BATCH_SIZE_S)
    else:
        logger.info("Initializing SimpleEmbeddingGen")
        embedding_gen = SimpleEmbeddingGen(cfg.EMBEDDING_MODEL_NAME, cfg.api.PYTORCH_DEVICE)
    app.state.embedding_gen = embedding_gen

    yield

    await app.state.db_pool.close()
    if cfg.api.ENABLE_EMBEDDING_BATCHING and embedding_gen:
        await embedding_gen.stop_worker()


app = FastAPI(lifespan=lifespan)

async def get_db(request: Request):
    async with request.app.state.db_pool.connection() as conn:
        yield conn

def get_retriever(request: Request):
    return request.app.state.retriever

def get_embedding_gen(request: Request):
    return request.app.state.embedding_gen

class NLQuery(BaseModel):
    query: str

@app.post("/search")
async def search_paper(query: NLQuery, conn=Depends(get_db), 
                 embedding_gen:EmbeddingGen=Depends(get_embedding_gen), 
                 retriever:Retriever=Depends(get_retriever)
                 ):
    embedding = await embedding_gen.embed(query.query)
    papers = await retriever.retrieve_and_rank(conn=conn, embedding=embedding)
    return {"papers": papers}