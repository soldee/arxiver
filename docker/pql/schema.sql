CREATE EXTENSION vector;

CREATE TABLE IF NOT EXISTS arxiv_meta (
    id bigserial PRIMARY KEY,
    title text NOT NULL,
    abstract text NOT NULL,
    embedding vector(768) NOT NULL,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || abstract)) STORED
);

CREATE INDEX fts_idx ON arxiv_meta USING GIN (fts);

