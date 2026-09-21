from fastapi import FastAPI
from pydantic import BaseModel
import psycopg2

from src.api.retrieval import Retriever
from src.core.config import cfg

app = FastAPI()

conn = psycopg2.connect(cfg.DB_URL)
retriever = Retriever(conn)

class NLQuery(BaseModel):
    query: str

@app.post("/search")
def search_paper(query: NLQuery):
    papers = retriever.retrieve_and_rank(query.query)
    return {"papers": papers}
