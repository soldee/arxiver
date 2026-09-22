from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from pydantic import BaseModel
import psycopg2.pool
import logging

from src.api.retrieval import Retriever
from src.core.config import cfg


logging.basicConfig(
    level=cfg.etl.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing PostgreSQL Connection Pool.")
    
    app.state.db_pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=cfg.api.MIN_DB_CONNECTIONS,
        maxconn=cfg.api.MAX_DB_CONNECTIONS,
        dsn=cfg.DB_URL
    )

    logger.info("Initializing Retriever for inference.")

    retriever = Retriever()
    app.state.retriever = retriever

    yield
    app.state.db_pool.closeall()


app = FastAPI(lifespan=lifespan)

def get_db(request: Request):
    conn = request.app.state.db_pool.getconn()
    try:
        yield conn
    finally:
        request.app.state.db_pool.putconn(conn)

def get_retriever(request: Request):
    return request.app.state.retriever

class NLQuery(BaseModel):
    query: str

@app.post("/search")
def search_paper(query: NLQuery, conn=Depends(get_db), retriever=Depends(get_retriever)):
    papers = retriever.retrieve_and_rank(conn=conn, query=query.query)
    return {"papers": papers}