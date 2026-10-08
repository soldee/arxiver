import json
import sys
from pathlib import Path
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cloud.ingestor import SQSRepo
from cloud.oai import OAIXMLParser


TEST_OAI_XML_PATH = ROOT / "tests" / "oai.xml"


class TestOAIXMLParser(unittest.TestCase):
    def test_parse_xml_extracts_valid_papers_from_fixture(self):
        xml = TEST_OAI_XML_PATH.read_bytes()

        papers = OAIXMLParser().parse_xml(xml)

        self.assertGreater(len(papers), 0)
        self.assertEqual(papers[0].id, "oai:arXiv.org:2501.00112")
        self.assertEqual(papers[0].datestamp, "2026-05-05")
        self.assertIn("QuadPiPS", papers[0].title)
        self.assertIn("QuadPiPS", papers[0].abstract)

    def test_parse_xml_skips_deleted_records(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/" xmlns:arxiv="http://arxiv.org/OAI/arXiv/">
          <ListRecords>
            <record>
              <header status="deleted">
                <identifier>oai:arXiv:deleted</identifier>
                <datestamp>2024-01-01</datestamp>
              </header>
            </record>
            <record>
              <header>
                <identifier>oai:arXiv:1234.5678</identifier>
                <datestamp>2024-01-02</datestamp>
              </header>
              <metadata>
                <arxiv:arXiv>
                  <arxiv:title>Alive title</arxiv:title>
                  <arxiv:abstract>Alive abstract</arxiv:abstract>
                </arxiv:arXiv>
              </metadata>
            </record>
          </ListRecords>
        </OAI-PMH>
        '''

        papers = OAIXMLParser().parse_xml(xml)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].id, "oai:arXiv:1234.5678")


class TestSQSRepo(unittest.TestCase):
    @mock.patch("cloud.ingestor.boto3.client")
    def test_consume_s3_creation_messages_processes_the_first_record_and_deletes_it(self, mock_boto_client):
        mock_sqs = mock.Mock()
        mock_sqs.get_queue_url.return_value = {"QueueUrl": "https://example.test/queue"}
        mock_sqs.receive_message.return_value = {
            "Messages": [
                {
                    "MessageId": "example-message-id-001",
                    "ReceiptHandle": "example-receipt-handle-001",
                    "MD5OfBody": "example-md5-of-body-001",
                    "Body": json.dumps({
                        "Records": [{
                            "eventVersion": "2.1",
                            "eventSource": "aws:s3",
                            "awsRegion": "us-east-1",
                            "eventTime": "2026-09-30T09:51:04.621Z",
                            "eventName": "ObjectCreated:Put",
                            "userIdentity": {"principalId": "example-principal-id"},
                            "requestParameters": {"sourceIPAddress": "127.0.0.1"},
                            "responseElements": {"x-amz-request-id": "example-request-id", "x-amz-id-2": "example-amz-id-2"},
                            "s3": {
                                "s3SchemaVersion": "1.0",
                                "configurationId": "example-config-id",
                                "bucket": {
                                    "name": "example-bucket",
                                    "ownerIdentity": {"principalId": "example-owner-id"},
                                    "arn": "arn:aws:s3:::example-bucket"
                                },
                                "object": {
                                    "key": "raw/2026/9/30/example.xml",
                                    "sequencer": "example-sequencer",
                                    "eTag": "example-etag",
                                    "size": 2692604
                                }
                            }
                        }]
                    }),
                }
            ]
        }
        mock_boto_client.return_value = mock_sqs

        with mock.patch("cloud.ingestor.cfg.cloud.SQS_QUEUE_NAME", "test-queue"), \
             mock.patch("cloud.ingestor.cfg.cloud.AWS_REGION", "us-east-1"):
            repo = SQSRepo()

        received = {}

        def process_func(bucket_name, object_key):
            received["bucket_name"] = bucket_name
            received["object_key"] = object_key

        repo.consumeS3CreationMessages(process_func)

        self.assertEqual(received, {"bucket_name": "example-bucket", "object_key": "raw/2026/9/30/example.xml"})
        mock_sqs.delete_message.assert_called_once_with(
            QueueUrl="https://example.test/queue",
            ReceiptHandle="example-receipt-handle-001",
        )

    @mock.patch("cloud.ingestor.boto3.client")
    def test_consume_s3_creation_messages_skips_malformed_records_payloads(self, mock_boto_client):
        mock_sqs = mock.Mock()
        mock_sqs.get_queue_url.return_value = {"QueueUrl": "https://example.test/queue"}
        mock_sqs.receive_message.return_value = {
            "Messages": [{
                "Body": '{"notRecords": [1, 2, 3]}',
                "ReceiptHandle": "receipt-malformed",
            }]
        }
        mock_boto_client.return_value = mock_sqs

        with mock.patch("cloud.ingestor.cfg.cloud.SQS_QUEUE_NAME", "test-queue"), \
             mock.patch("cloud.ingestor.cfg.cloud.AWS_REGION", "us-east-1"):
            repo = SQSRepo()

        received = {}

        def process_func(bucket_name, object_key):
            received["bucket_name"] = bucket_name
            received["object_key"] = object_key

        repo.consumeS3CreationMessages(process_func)

        self.assertEqual(received, {})
        mock_sqs.delete_message.assert_not_called() 


if __name__ == "__main__":
    unittest.main()
