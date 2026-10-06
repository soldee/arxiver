from src.core.config import cfg

import boto3
import psycopg
import logging
import requests
import datetime
import io
import time
from typing import BinaryIO
from xml.etree import ElementTree as ET


class Harvester:

    def __init__(self, conn, rate_limit_seconds: float = 4.):
        self.logger = logging.getLogger(__name__)
        
        self.bucket_name: str = cfg.cloud.S3_BUCKET_NAME
        region_name: str = cfg.cloud.AWS_REGION
        self.s3_client = boto3.client('s3', region_name=region_name)
        
        self.conn = conn

        self._last_request_time: float = 0.
        self.rate_limit_seconds: float = rate_limit_seconds


    def stream_to_s3(self, file: BinaryIO, set_name: str):
        now = datetime.datetime.now(datetime.timezone.utc)
        key = f"raw/{now.year}/{now.month}/{now.day}/{now.timestamp():.6f}.xml"

        self.s3_client.upload_fileobj(
            Fileobj=file,
            Bucket=self.bucket_name,
            Key=key,
            ExtraArgs={'Metadata': {'set_name': set_name}}
        )

    def save_error_s3(self, xml_bytes: bytes, reason: str):
        now = datetime.datetime.now(datetime.timezone.utc)
        key = f"error/{now.timestamp():.6f}.xml"
        safe_reason = reason.encode("ascii", "replace").decode("ascii")[:256]

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=xml_bytes,
            Metadata={"reason": safe_reason}
        )

    def _save_error_safely(self, xml_bytes: bytes, reason: str):
        try:
            self.save_error_s3(xml_bytes, reason)
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


    def _parse_resumables_xml(
        self, xml_bytes: bytes
    ) -> tuple[str | None, datetime.datetime | None, str | None, str | None]:
        namespaces = {
            'arxiv': 'http://arxiv.org/OAI/arXiv/',
            'oai':'http://www.openarchives.org/OAI/2.0/'
        }

        new_resume_token = None
        expiration_date = None
        last_datestamp = None

        parser = ET.fromstring(xml_bytes)

        error_elem = parser.find("./oai:error", namespaces=namespaces)
        if error_elem is not None:
            code = error_elem.get("code", "unknown")
            raise ValueError(f"OAI-PMH error {code}: {error_elem.text or ''}")

        resumeToken_elem = parser.find(".//oai:resumptionToken", namespaces=namespaces)
        
        if resumeToken_elem is not None:
            new_resume_token = resumeToken_elem.text or None
            expiration_date = resumeToken_elem.get("expirationDate")
            expiration_date = (
                datetime.datetime.fromisoformat(expiration_date)
                if expiration_date is not None else None
            )
        
        headers = parser.findall(
            "./oai:ListRecords/oai:record/oai:header", namespaces=namespaces
        )
        if headers:
            last_datestamp = headers[-1].findtext("oai:datestamp", namespaces=namespaces)
        
        return new_resume_token, expiration_date, last_datestamp, None

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
            new_resume_token, new_token_expire_date, last_datestamp, error = self._parse_resumables_xml(res_bytes)
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
            self.stream_to_s3(io.BytesIO(res_bytes), set_name=set_name)
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
    logging.basicConfig(
        level=cfg.cloud.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    with psycopg.connect(cfg.DB_URL) as conn:
        Harvester(conn).run("cs:cs:RO")
