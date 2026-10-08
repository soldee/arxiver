import boto3
from botocore.exceptions import ClientError
import logging
import datetime
from typing import BinaryIO

from src.core.config import cfg

class S3Repo:

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        region_name: str = cfg.cloud.AWS_REGION
        self.bucket_name: str = cfg.cloud.S3_BUCKET_NAME
        
        self.s3_client = boto3.client('s3', region_name=region_name)

    def stream_oaixml_to_s3(self, file: BinaryIO, set_name: str):
        now = datetime.datetime.now(datetime.timezone.utc)
        key = f"raw/{now.year}/{now.month}/{now.day}/{now.timestamp():.6f}.xml"

        self.s3_client.upload_fileobj(
            Fileobj=file,
            Bucket=self.bucket_name,
            Key=key,
            ExtraArgs={'Metadata': {'set_name': set_name}}
        )

    def save_oaixml_error_s3(self, xml_bytes: bytes, reason: str):
        now = datetime.datetime.now(datetime.timezone.utc)
        key = f"error/{now.timestamp():.6f}.xml"
        safe_reason = reason.encode("ascii", "replace").decode("ascii")[:256]

        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=xml_bytes,
            Metadata={"reason": safe_reason}
        )

    def get_oai_file(self, bucket_name: str, object_key: str) -> tuple[bytes, str]:
        try:
            response = self.s3_client.head_object(Bucket=bucket_name, Key=object_key)
            set_name = response['Metadata'].get('set_name')
            if not set_name:
                self.logger.error("Missing set_name metadata for S3 object: %s/%s", bucket_name, object_key)
                return
        except ClientError as e:
            self.logger.error("Failed to retrieve metadata for S3 object: %s/%s, error: %s", bucket_name, object_key, e)
            raise

        try:
            s3_object = self.s3_client.get_object(Bucket=bucket_name, Key=object_key)
            raw_response = s3_object['Body']

            self.logger.info("Processing S3 object: %s/%s", bucket_name, object_key)
            return raw_response.read(), set_name

        except ClientError as e:
            self.logger.error("Failed to retrieve S3 object: %s/%s, error: %s", bucket_name, object_key, e)
            raise
