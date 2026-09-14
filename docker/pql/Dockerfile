FROM postgres:18.6

RUN apt update && apt install -y \
git \
build-essential \
postgresql-server-dev-18

WORKDIR /tmp

# https://github.com/pgvector/pgvector
RUN git clone --branch v0.8.6 https://github.com/pgvector/pgvector.git
WORKDIR /tmp/pgvector
RUN make && make install

