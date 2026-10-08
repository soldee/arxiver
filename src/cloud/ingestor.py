from src.etl.ingestor.fetcher import Paper
from src.cloud.oai import OAIXMLParser
from src.cloud.s3repo import S3Repo
from src.etl.ingestor.embeddings import EmbeddingsGen
from src.etl.ingestor.repository import PaperRepository
from src.core.config import cfg

import boto3
from botocore.exceptions import ClientError
import json
import logging
import psycopg

class SQSRepo:

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        sqs_qname: str = cfg.cloud.SQS_QUEUE_NAME
        region_name: str = cfg.cloud.AWS_REGION
        
        self.sqs = boto3.client('sqs', region_name=region_name)
        self.queue_url = self.sqs.get_queue_url(QueueName=sqs_qname)['QueueUrl']

    def consumeS3CreationMessages(self, process_func):
        try:
            response = self.sqs.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=1,
                # long polling for eliminating false empty responses (see https://docs.aws.amazon.com/boto3/latest/guide/sqs-example-long-polling.html)
                WaitTimeSeconds=5,
                VisibilityTimeout=60
            )
        except ClientError as e:
            self.logger.error("Failed to receive messages from SQS: %s", e)
            return

        messages = response.get('Messages', [])
        if not messages:
            self.logger.info("No messages received from SQS.")
            return

        for msg in messages:            
            body = msg.get('Body')
            if body is None:
                self.logger.warning("Received message with empty body, skipping. Message: %s", msg)
                continue

            receipt_handle = msg.get('ReceiptHandle')
            if receipt_handle is None:
                self.logger.warning("Received message with no ReceiptHandle, skipping. Message: %s", msg)
                continue

            try:
                if isinstance(body, str):
                    body = json.loads(body)
                if not isinstance(body, dict):
                    self.logger.warning("Received message body with unexpected type %s, skipping. Message: %s", type(body).__name__, msg)
                    continue

                records = body.get('Records', [])
                if not records:
                    self.logger.warning("Records not found in message body, skipping. Message: %s", msg)
                    continue

                if len(records) == 0:
                    self.logger.warning("No Records found in message body, skipping. Message: %s", msg)
                    continue
                
                if len(records) > 1:
                    self.logger.warning("Multiple Records found in message body, processing only the first one.")

                record = records[0]
                s3_info = record.get('s3', {})
                bucket_name = s3_info.get('bucket', {}).get('name')
                object_key = s3_info.get('object', {}).get('key')

                if not bucket_name or not object_key:
                    self.logger.warning("Missing bucket name or object key in S3 info, skipping. Message: %s", msg)
                    continue

                self.logger.info("Fetching key '%s' in bucket name '%s'", object_key, bucket_name)

                process_func(bucket_name, object_key)

            except (TypeError, ValueError, KeyError) as e:
                self.logger.error("Missing expected key in message body: {e}. Message: %s", e, msg)
                continue
            except Exception as e:
                self.logger.error("Unexpected error while processing message: %s. Message: %s", e, msg)
                raise

            try:
                self.sqs.delete_message(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=receipt_handle
                )
            except Exception as e:
                self.logger.error("Failed to delete message from SQS after processing. ReceiptHandle='%s'. Error: %s. Message: %s", receipt_handle, e, msg)


class PaperIngestionPipeline:

    def __init__(self, sqs_repo: SQSRepo, s3_repo: S3Repo, oai_parser: OAIXMLParser, embeddings_gen: EmbeddingsGen, papers_repo: PaperRepository):
        self.logger = logging.getLogger(__name__)

        self.sqs_repo = sqs_repo
        self.s3_repo = s3_repo
        self.oai_parser = oai_parser
        self.embeddings_gen = embeddings_gen
        self.papers_repo = papers_repo

    def process(self, bucket_name: str, object_key: str):
        try:
            file, set_name = self.s3_repo.get_oai_file(bucket_name, object_key)
            self.logger.info("Found file in S3 with set: %s", set_name)
        except Exception as e:
            self.logger.error("Failed to retrieve OAI XML file from S3, error: %s", e)
            raise

        try:
            papers: list[Paper] = self.oai_parser.parse_xml(file)
        except Exception as e:
            self.logger.error("Failed to extract papers when parsing OAI XML file, error: %s", e)
            raise

        try:
            for chunk, emb in self.embeddings_gen.generate_embeddings(papers):
                self.papers_repo.insert_batch_with_embeddings(zip(chunk, emb.tolist()))
        except Exception as e:
            self.logger.error("Failed to generate and insert embeddings, error: %s", e)
            raise

    def run(self):
        self.logger.info("Starting PaperIngestorPipeline.run()")
        self.sqs_repo.consumeS3CreationMessages(process_func=self.process)

if __name__ == "__main__":
    logging.basicConfig(
        level=cfg.cloud.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    logger = logging.getLogger(__name__)
    logger.info("Initializing dependencies")

    sqs_repo = SQSRepo()
    s3_repo = S3Repo()
    oai_parser = OAIXMLParser()
    
    with psycopg.connect(cfg.DB_URL) as conn:
        emb_gen = EmbeddingsGen(cfg.etl.MODEL_BATCH_SIZE)
        repo = PaperRepository(conn)
        
        ingestor = PaperIngestionPipeline(
            sqs_repo=sqs_repo,
            s3_repo=s3_repo, 
            oai_parser=oai_parser,
            embeddings_gen=emb_gen, 
            papers_repo=repo
        )
        ingestor.run()
