from src.core.config import cfg
from src.cloud.s3repo import S3Repo
from src.cloud.oai import OAIXMLParser

import argparse
import psycopg
import logging
import requests
import datetime
import io
import time
from xml.etree import ElementTree as ET

class PaperHarvestingPipeline:

    def __init__(self, conn, oai_parser: OAIXMLParser, s3_repo: S3Repo, rate_limit_seconds: float = 4.):
        self.logger = logging.getLogger(__name__)
        
        self.conn = conn
        self.oai_parser = oai_parser
        self.s3_repo = s3_repo

        self._last_request_time: float = 0.
        self.rate_limit_seconds: float = rate_limit_seconds

    def _save_error_safely(self, xml_bytes: bytes, reason: str):
        try:
            self.s3_repo.save_oaixml_error_s3(xml_bytes, reason)
        except Exception:
            self.logger.exception("Failed to save the XML response to the S3 error path")

    def _enforce_rate_limits(
        self,
        extra_delay: float = 0,
        last_request_at: datetime.datetime | None = None,
    ):
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if last_request_at is not None:
            persisted_elapsed = (
                datetime.datetime.now(datetime.timezone.utc) - last_request_at
            ).total_seconds()
            elapsed = min(elapsed, persisted_elapsed)
        target_delay = max(self.rate_limit_seconds, extra_delay)

        if elapsed < target_delay:
            wait_time = target_delay - elapsed
            self.logger.info("Rate limit delay: sleeping for %.2fs", wait_time)
            time.sleep(wait_time)

        self._last_request_time = time.monotonic()


    def request_batch(
        self,
        set_name: str,
        resumable_token: str | None,
        resumable_datestamp: str | None,
        last_request_at: datetime.datetime | None = None,
    ) -> requests.Response:
        self._enforce_rate_limits(last_request_at=last_request_at)

        url: str = cfg.etl.ARXIV_OAIMPH_URL

        self.logger.info("Fetching arXiv's OAI PMH for set '%s'", set_name)

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

        res = requests.get(url, params=params, timeout=120)
        if res.status_code == 503:
            retry_after = int(res.headers.get("Retry-After", 20))
            self.logger.warning("HTTP 503 received. Respecting Retry-After: %ds", retry_after)
            res.close()
            self._enforce_rate_limits(extra_delay=retry_after)

        res.raise_for_status()
        return res

    def _fetch_resumables(self, set_name: str):
        with self.conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, resume_token, token_expire_date, resume_datestamp, inserted_at
                FROM {cfg.POSTGRES_RESUMABLES_TABLE}
                WHERE set = %s 
                ORDER BY inserted_at DESC LIMIT 1;
            """, (set_name, ))

            x = cur.fetchone()
            id, resume_token, token_expire_date, resume_datestamp, inserted_at = (
                None, None, None, None, None
            )
            if x is not None:
                id, resume_token, token_expire_date, resume_datestamp, inserted_at = x
                self.logger.info("Fetched resumables for set '%s': id=%s", set_name, id)
            else:
                self.logger.warning("No resumables found for set '%s'", set_name)

            return id, resume_token, token_expire_date, resume_datestamp, inserted_at

    def _commit_resumables(self, set_name, new_resume_token, new_token_expire_date, last_datestamp):
        self.logger.info("new_resume_token=%s, last_datestamp=%s", new_resume_token, last_datestamp)
        try:
            with self.conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {cfg.POSTGRES_RESUMABLES_TABLE}
                        (set, resume_token, token_expire_date, resume_datestamp)
                    VALUES (%s, %s, %s, %s)
                """, (set_name, new_resume_token, new_token_expire_date, last_datestamp))
                    
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        
    def run(self, set_name: str):
        try:
            id, resume_token, token_expire_date, resume_datestamp, inserted_at = (
                self._fetch_resumables(set_name=set_name)
            )
        except Exception:
            self.logger.exception("Error fetching resumables for set '%s'", set_name)
            raise

        try:
            with self.request_batch(
                set_name=set_name,
                resumable_token=resume_token,
                resumable_datestamp=resume_datestamp,
                last_request_at=inserted_at,
            ) as res:
                res_bytes = res.content
        except requests.exceptions.RequestException:
            self.logger.exception("Error fetching arXiv's OAI-PMH response for set '%s'", set_name)
            raise

        self.logger.info("Received response, parsing XML to extract resumables")
        try:
            new_resume_token, new_token_expire_date, last_datestamp, error = self.oai_parser.parse_xml_resumables(res_bytes)
        except (ET.ParseError, ValueError):
            reason = "Error parsing OAI-PMH XML response"
            self.logger.exception(reason)
            self._save_error_safely(res_bytes, reason)
            raise

        self.logger.info("Checking resumable's status")
        if new_resume_token is None and last_datestamp is None:
            reason = "OAI-PMH response contains neither a resumption token nor a record datestamp"
            self.logger.error(reason)
            self._save_error_safely(res_bytes, reason)
            raise ValueError(reason)
                    
        self.logger.info("Streaming XML to S3")
        try:
            self.s3_repo.stream_oaixml_to_s3(io.BytesIO(res_bytes), set_name=set_name)
        except Exception:
            reason = "Error streaming OAI-PMH response to S3"
            self.logger.exception(reason)
            self._save_error_safely(res_bytes, reason)
            raise

        self.logger.info("Commiting resumables")
        try:
            self._commit_resumables(
                set_name, new_resume_token, new_token_expire_date, last_datestamp
            )
        except Exception:
            reason = "Error committing resumables to PostgreSQL"
            self.logger.exception(reason)
            self._save_error_safely(res_bytes, reason)
            raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser("arxiver")
    parser.add_argument(
        'list_set', type=str,
        help="Specific category for selective harvesting. See https://oaipmh.arxiv.org/oai?verb=ListSets. E.g. 'cs:cs:RO' to harvest robotics papers"
    )
    args = parser.parse_args()
    set_name = args.list_set
    
    logging.basicConfig(
        level=cfg.cloud.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    oai_parser = OAIXMLParser()
    s3_repo = S3Repo()
    
    with psycopg.connect(cfg.DB_URL) as conn:
        pipeline = PaperHarvestingPipeline(
            conn=conn,
            oai_parser=oai_parser,
            s3_repo=s3_repo
        )
        pipeline.run(set_name=set_name)
