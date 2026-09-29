from src.etl.ingestor.fetcher import ArxivOaiFetcher, Resumables
from src.core.config import cfg

import boto3
from botocore.exceptions import ClientError
import psycopg
import logging
import requests
import datetime

class Harvester:

    def __init__(self, conn):
        self.logger = logging.getLogger(__name__)
        resumables = Resumables(conn)
        self.fetcher = ArxivOaiFetcher(conn, resumables)

        self.bucket_name: str = cfg.cloud.S3_BUCKET_NAME
        region_name: str = cfg.cloud.AWS_REGION
        self.s3_client = boto3.client('s3', region_name=region_name)

    def stream_to_s3(self, raw_response):
        raw_response.decode_content = True
        
        now = datetime.datetime.now()
        key = f"raw/{now.year}/{now.month}/{now.day}/{int(now.timestamp())}.xml"

        try:
            self.s3_client.upload_fileobj(
                Fileobj=raw_response,
                Bucket=self.bucket_name,
                Key=key
            )
        except ClientError as e:
            self.logger.error("Error streaming XML response to S3: %s", e)
            raise

    def run(self, set_name: str):
        try:
            res: requests.Response = self.fetcher.request_batch(set_name=set_name)
            res.raise_for_status()
            
            self.logger.info("Streaming XML to S3")
            self.stream_to_s3(res.raw)
        except requests.exceptions.Timeout as err:
            self.logger.error("Request timed out: %s", str(err))
        except requests.exceptions.HTTPError as err:
            self.logger.error("External service returned an HTTP error: %s", str(err))
        except requests.exceptions.RequestException as err:
            self.logger.exception("Failed to connect to external service: %s", str(err))

if __name__ == "__main__":
    logging.basicConfig(
        level=cfg.cloud.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    with psycopg.connect(cfg.DB_URL) as conn:
        Harvester(conn).run("cs:cs:RO")
