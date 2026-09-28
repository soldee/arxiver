from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from pydantic import BaseModel
from psycopg_pool import AsyncConnectionPool
import logging
from pyinstrument import Profiler

from src.api.retrieval import EmbeddingGen, SimpleEmbeddingGen, BatchedEmbeddingGen, Retriever, Reranker
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

    embedding_gen: EmbeddingGen
    if cfg.api.ENABLE_EMBEDDING_BATCHING:
        logger.info("Initializing BatchedEmbeddingGen")
        embedding_gen = BatchedEmbeddingGen(cfg.EMBEDDING_MODEL_NAME, cfg.api.PYTORCH_DEVICE)
        await embedding_gen.start_worker(cfg.api.EMBEDDING_MAX_LATENCY_S, cfg.api.EMBEDDING_MAX_BATCH_SIZE_S)
    else:
        logger.info("Initializing SimpleEmbeddingGen")
        embedding_gen = SimpleEmbeddingGen(cfg.EMBEDDING_MODEL_NAME, cfg.api.PYTORCH_DEVICE)

    reranker = Reranker(
        model_name=cfg.api.RERANKER_MODEL_NAME, 
        model_device=cfg.api.PYTORCH_DEVICE,
        model_max_len=cfg.api.RERANKER_MODEL_MAX_LEN
    )

    logger.info("Initializing Retriever for inference.")
    retriever = Retriever(
        embedding_gen=embedding_gen, 
        reranker=reranker
    )
    app.state.retriever = retriever

    yield

    await app.state.db_pool.close()
    if cfg.api.ENABLE_EMBEDDING_BATCHING and embedding_gen:
        await embedding_gen.stop_worker()


app = FastAPI(lifespan=lifespan)

if cfg.api.PROFILER:
    @app.middleware("http")
    async def profile_request(request: Request, call_next):
        profiler = Profiler()
        profiler.start()        
        response = await call_next(request)
        profiler.stop()
        
        print(profiler.output_text(unicode=True, color=True))
        return response

async def get_db(request: Request):
    async with request.app.state.db_pool.connection() as conn:
        yield conn

def get_retriever(request: Request):
    return request.app.state.retriever


@app.get("/health")
def health_check():
    return {"status": "healthy"}

class NLQuery(BaseModel):
    query: str

@app.post("/search")
async def search_paper(query: NLQuery, conn=Depends(get_db), retriever:Retriever=Depends(get_retriever)):
    papers = await retriever.search(conn, query.query, cfg.api.RESULTS_LIMIT)
    return {"papers": papers}