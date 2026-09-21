from src.core.config import cfg

import requests
from xml.etree import ElementTree as ET
import logging
import time
from datetime import datetime

class Paper:
    def __init__(self, id: str, datestamp, title: str, abstract: str):
        self.id = id
        self.datestamp = datestamp
        self.title = title
        self.abstract = abstract

    def __str__(self):
        return f"id={self.id}, datestamp={self.datestamp}\n{self.title}\n{self.abstract}"


class ResumableItem:
    def __init__(self, resume_token: str | None, token_expire_date: datetime | None, resume_datestamp: str | None, 
               datestamp_expire_date: datetime | None):
        self.resume_token = resume_token
        self.token_expire_date = token_expire_date
        self.resume_datestamp = resume_datestamp
        self.datestamp_expire_date = datestamp_expire_date

    def __str__(self):
        return f"""
        resume_token={self.resume_token}, token_expire_date={self.token_expire_date}, resume_datestamp={self.resume_datestamp}, 
        datestamp_expire_date={self.datestamp_expire_date}
        """


class Resumables:
    def __init__(self, conn):
        self.logger = logging.getLogger(__name__)
        self.conn = conn

        self.set_resumables: dict[str, ResumableItem] = {}
        
        self._fetch()

    def _fetch(self):
        self.logger.info("Fetching resumables...")

        with self.conn.cursor() as cur:
            cur.execute(f"""
                SELECT set, resume_token, token_expire_date, resume_datestamp, datestamp_expire_date
                FROM {cfg.POSTGRES_RESUMABLES_TABLE}
            """)

            for x in cur.fetchall():
                item = ResumableItem(x[1], x[2], x[3], x[4])
                self.set_resumables[x[0]] = item

        self.logger.info("Fetched %d ListSet resumables for sets: %s", len(self.set_resumables), ', '.join(self.set_resumables.keys()))

    def commit(self, set_name: str):
        """
        Resumables have to be persisted after the papers have been persisted properly. This way, if there is an error when 
        processing papers, we can resume from the last persisted token/datestamp. So the responsibility of "commiting" the 
        papers is offloaded to the consumer
        """
        item = self.set_resumables.get(set_name)
        if not item:
            self.logger.error("No resumable item found to persist for set_name=%s", set_name)
            return

        with self.conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {cfg.POSTGRES_RESUMABLES_TABLE}
                (set, resume_token, token_expire_date, resume_datestamp, datestamp_expire_date)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (set) DO UPDATE
                SET resume_token=excluded.resume_token, token_expire_date=excluded.token_expire_date,
                    resume_datestamp=excluded.resume_datestamp, datestamp_expire_date=excluded.datestamp_expire_date
            """, (set_name, item.resume_token, item.token_expire_date, item.resume_datestamp, item.datestamp_expire_date))
            self.conn.commit()

    def get(self, set_name: str) -> tuple[str | None]:
        """
        Get resumable tokens from dict, checking if expire dates are valid. 
        Token's expire date is considered valid if it is greater than the current date.
        Datestamp's expire date is considered valid if it is None or greater than the current date.
        """

        item = self.set_resumables.get(set_name)
        if not item:
            return None, None

        now = datetime.now(cfg.etl.TIMEZONE)
        token: str | None = None
        datestamp: str | None = None

        print(f"resume_token={item.resume_token}, token_expire_date={item.token_expire_date}, now={now}")

        if item.resume_token and item.token_expire_date and item.token_expire_date > now:
            token = item.resume_token

        if item.resume_datestamp and (not item.datestamp_expire_date or item.datestamp_expire_date > now):
            datestamp = item.resume_datestamp

        return token, datestamp

    def set(self, set_name: str, resume_token: str|None, token_expire_date:datetime|None, resume_datestamp: str|None, 
            datestamp_expire_date: datetime|None = None):
        item = self.set_resumables.get(set_name)

        if not item or resume_datestamp:
            new_item = ResumableItem(resume_token, token_expire_date, resume_datestamp, datestamp_expire_date)
        else:
            new_item = ResumableItem(resume_token, token_expire_date, item.datestamp, item.datestamp_expire_date)
            
        self.set_resumables[set_name] = new_item


class ArxivOaiFetcher:

    def __init__(self, conn, resumables: Resumables, rate_limit_seconds: float = 4.0):
        self.conn = conn
        self.logger = logging.getLogger(__name__)

        self._last_request_time: float = 0
        self.rate_limit_seconds = rate_limit_seconds
        self._resumables = resumables


    def _enforce_rate_limits(self, extra_delay: float = 0):
        now = time.monotonic()
        elapsed = now - self._last_request_time
        target_delay = max(self.rate_limit_seconds, extra_delay)

        if elapsed < target_delay:
            wait_time = target_delay - elapsed
            self.logger.info("Rate limit delay: sleeping for %.2fs", wait_time)
            time.sleep(wait_time)

        self._last_request_time = time.monotonic()

    def request_batch(self, set_name: str) -> list[Paper]:
        self._enforce_rate_limits()

        url: str = cfg.etl.ARXIV_OAIMPH_URL

        self.logger.info("Fetching for set '%s'", set_name)

        params = {}
        resumable_token, resumable_datestamp = self._resumables.get(set_name)
        if resumable_token is not None:
            params = {
                'verb': 'ListRecords',
                'resumptionToken': resumable_token
            }
        else:
            params = {
                'verb':'ListRecords', 
                'set':set_name, 
                'metadataPrefix':'arXiv'
            }
            if resumable_datestamp is not None:
                params["from"] = resumable_datestamp

        try:
            res = requests.get(url, params=params, timeout=120, stream=True)
            if res.status_code == 503:
                retry_after = int(res.headers.get("Retry-After", 20))
                self.logger.warning("HTTP 503 received. Respecting Retry-After: %ds", retry_after)
                res.close()
                self._enforce_rate_limits(extra_delay=retry_after)

            res.raise_for_status()
            return self._parse_stream(res, set_name)
        except requests.exceptions.Timeout as err:
            self.logger.error("Request timed out: %s", str(err))
        except requests.exceptions.HTTPError as err:
            self.logger.error("External service returned an HTTP error: %s", str(err))
        except requests.exceptions.RequestException as err:
            self.logger.exception("Failed to connect to external service: %s", str(err))

        return []


    def _parse_stream(self, response, set_name: str):
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

        self.logger.info("Found %d papers for set: %s", len(papers), set_name)

        last_datestamp = papers[-1].datestamp

        self._resumables.set(set_name, new_resume_token, datetime.fromisoformat(expiration_date), last_datestamp)

        return papers
