from config import cfg
import requests
from xml.etree import ElementTree as ET
import logging
import time

class Paper:
    def __init__(self, id: str, datestamp, title: str, abstract: str):
        self.id = id
        self.datestamp = datestamp
        self.title = title
        self.abstract = abstract


class ArxivOaiFetcher:

    def __init__(self, conn, rate_limit_seconds: float = 4.0):
        self.conn = conn
        self.logger = logging.getLogger(__name__)

        self.resume_token: str | None = None
        self.token_expiration_date: str | None = None
        self.last_datestamp: str | None = None

        self._last_request_time: float = 0
        self.rate_limit_seconds = rate_limit_seconds

        self._fetch_resume_token()

    def _fetch_resume_token(self):
        self.logger.info('Fetching resumables...')

        cur = self.conn.cursor()
        cur.execute(
            f"SELECT value FROM {cfg.POSTGRES_RESUMABLES_TABLE} WHERE id = 'resume_token' AND (expire_date > NOW() OR expire_date IS NULL)"
        )
        fetched_token = cur.fetchone()
        cur.close()

        if fetched_token is not None:
            self.resume_token = str(fetched_token[0])
            self.logger.info('Resuming ingestion via resume_token: %s', self.resume_token)
            return
        else:
            self.logger.info('No resume_token available. Searching for last datestamp to resume from')
            cur = self.conn.cursor()
            cur.execute(
                f"SELECT value FROM {cfg.POSTGRES_RESUMABLES_TABLE} WHERE id = 'last_datestamp' AND (expire_date > NOW() OR expire_date IS NULL)"
            )
            datestamp = cur.fetchone()
            cur.close()

            if datestamp is None:
                self.logger.info('No last_datestamp available. Starting ingestion from scratch.')
            else:
                self.last_datestamp = str(datestamp[0])
                self.logger.info('Resuming from last_datestamp: %s', self.last_datestamp)


    def _upsert_resume_token(self, resume_token, expire_date):
        self.logger.info('Upserting resumable resume_token=%s with expire_date=%s', resume_token, expire_date)

        cur = self.conn.cursor()
        cur.execute(f"""
            INSERT INTO {cfg.POSTGRES_RESUMABLES_TABLE} 
            (id, value, expire_date) VALUES ('resume_token', %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET value = excluded.value, expire_date = excluded.expire_date;
        """, (resume_token, expire_date)
        )
        self.conn.commit()
        cur.close()

    def _upsert_datestamp(self, datestamp):
        self.logger.info('Upserting resumable datestamp=%s', datestamp)

        cur = self.conn.cursor()
        cur.execute(f"""
            INSERT INTO {cfg.POSTGRES_RESUMABLES_TABLE} 
            (id, value) VALUES ('last_datestamp', %s)
            ON CONFLICT (id) DO UPDATE
            SET value = excluded.value;
        """, (datestamp,)
        )
        self.conn.commit()
        cur.close()

    def _expire_datestamp(self):
        cur = self.conn.cursor()
        cur.execute(f"""
            DELETE FROM {cfg.POSTGRES_RESUMABLES_TABLE} WHERE id = 'last_datestamp'
        """)
        cur.close()   

    def update_resumables(self):
        if self.resume_token:
            self._upsert_resume_token(self.resume_token, self.token_expiration_date)

        if self.last_datestamp:
            self._upsert_datestamp(self.last_datestamp)
        else:
            self._expire_datestamp()


    def _enforce_rate_limits(self):
        now = time.monotonic()
        elapsed = now - self._last_request_time

        if elapsed < self.rate_limit_seconds:
            wait_time = self.rate_limit_seconds - elapsed
            self.logger.info("Rate limit delay: sleeping for %.2fs", wait_time)
            time.sleep(wait_time)

        self._last_request_time = time.monotonic()

    def request_batch(self) -> list[Paper]:
        self._enforce_rate_limits()

        url: str = cfg.ARXIV_OAIMPH_URL

        params = {}
        if self.resume_token is not None:
            params = {
                'verb': 'ListRecords',
                'resumptionToken': self.resume_token
            }
        else:
            params = {
                'verb':'ListRecords', 
                'set':'cs:cs:AI', 
                'metadataPrefix':'arXiv'
            }
            if self.last_datestamp is not None:
                params["from"] = self.last_datestamp

        try:
            res = requests.get(url, params=params, timeout=120, stream=True)
            res.raise_for_status()
            return self._parse_stream(res)
        except requests.exceptions.Timeout as err:
            self.logger.error("Request timed out: %s", str(err))
        except requests.exceptions.HTTPError as err:
            self.logger.error("External service returned an HTTP error: %s", str(err))
        except requests.exceptions.RequestException as err:
            self.logger.exception("Failed to connect to external service: %s", str(err))

        return []


    def _parse_stream(self, response):
        namespaces = {
            'arxiv': 'http://arxiv.org/OAI/arXiv/',
            'oai':'http://www.openarchives.org/OAI/2.0/'
        }

        papers: list[Paper] = []
        new_resume_token = None
        expiration_date = None

        # start events are tag openings, end events are tag closings
        # we want to look for closings of </record> to process records one by one and clear the memory after parsing them
        parser = ET.XMLPullParser(events=("start", "end"))

        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                parser.feed(chunk)
                
                for event, elem in parser.read_events():
                    if event == 'end' and elem.tag == f"{{{namespaces['oai']}}}record":
                        header = elem.find("./oai:header", namespaces)

                        # skip deleted records
                        if header is not None and header.get("status") == "deleted":
                            elem.clear()
                            continue

                        datestamp = elem.findtext("./oai:header/oai:datestamp", default="", namespaces=namespaces)
                        id = elem.findtext(".//arxiv:id", default="", namespaces=namespaces)
                        title = elem.findtext(".//arxiv:title", default="", namespaces=namespaces)
                        abstract = elem.findtext(".//arxiv:abstract", default="", namespaces=namespaces)

                        papers.append(Paper(id=id, datestamp=datestamp, title=title, abstract=abstract))

                        # clear record from memory
                        elem.clear()

                    elif event == 'end' and elem.tag == f"{{{namespaces['oai']}}}resumptionToken":
                        new_resume_token = elem.text
                        expiration_date = elem.get("expirationDate")
                        elem.clear()

        parser.close()

        if len(papers) == 0:
            self.logger.error('No papers found')
            return papers

        self.logger.info("Found %d papers", len(papers))

        # resumables have to be persisted after the papers have been persisted properly
        # this way, if there is an error when processing papers, we can resume from the last persisted token/datestamp
        # so the responsibility of "commiting" the papers is offloaded to the consumer
        if new_resume_token:
            self.resume_token = new_resume_token
            self.token_expiration_date = expiration_date
        else:
            self.resume_token = None
            self.token_expiration_date = None

        last_datestamp = papers[-1].datestamp
        if last_datestamp:
            self.last_datestamp = last_datestamp

        return papers
