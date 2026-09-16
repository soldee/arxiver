from config import cfg
import psycopg2
import requests
from xml.etree import ElementTree as ET
import logging

class Paper:
    def __init__(self, datestamp, title: str, abstract: str):
        self.datestamp = datestamp
        self.title = title
        self.abstract = abstract


class Ingestor:

    def __init__(self, conn):
        self.conn = conn
        self.logger = logging.getLogger(__name__)
        self.resume_token: str | None = None
        self.last_datestamp: str | None = None

        self.__fetch_resume_token__()

    def __fetch_resume_token__(self):
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


    def __upsert_resume_token__(self, resume_token, expire_date):
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

    def __upsert_datestamp__(self, datestamp):
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

    def __expire_datestamp__(self):
        cur = self.conn.cursor()
        cur.execute(f"""
            DELETE FROM {cfg.POSTGRES_RESUMABLES_TABLE} WHERE id = 'last_datestamp'
        """)
        cur.close()   

    def request_batch(self) -> list[Paper]:
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
                'metadataPrefix':'arXiv',
                'from': self.last_datestamp
            }
            if self.last_datestamp is not None:
                params["from"] = self.last_datestamp

        try:
            res = requests.get(url, params=params, timeout=120, stream=True)
            res.raise_for_status()
            return self.__parse_stream__(res)
        except requests.exceptions.Timeout as err:
            self.logger.error("Request timed out: %s", str(err))
        except requests.exceptions.HTTPError as err:
            self.logger.error("External service returned an HTTP error: %s", str(err))
        except requests.exceptions.RequestException as err:
            self.logger.exception("Failed to connect to external service: %s", str(err))

        return []


    def __parse_stream__(self, response):
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

                        datestamp_node = elem.find("./oai:header/oai:datestamp", namespaces)
                        title_node = elem.find(".//arxiv:title", namespaces)
                        abstract_node = elem.find(".//arxiv:abstract", namespaces)

                        datestamp = datestamp_node.text if datestamp_node is not None else ""
                        title = title_node.text.strip() if title_node is not None and title_node.text else ""
                        abstract = abstract_node.text.strip() if abstract_node is not None and abstract_node.text else ""

                        papers.append(Paper(datestamp=datestamp, title=title, abstract=abstract))

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

        if new_resume_token:
            self.resume_token = new_resume_token
            self.__upsert_resume_token__(self.resume_token, expiration_date)
        else:
            self.resume_token = None

        last_datestamp = papers[-1].datestamp
        if last_datestamp:
            self.__upsert_datestamp__(papers[-1].datestamp)
        else:
            self.__expire_datestamp__()

        return papers
