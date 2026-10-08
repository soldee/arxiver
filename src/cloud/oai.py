import logging
from xml.etree import ElementTree as ET
import datetime

from src.etl.ingestor.fetcher import Paper

class OAIXMLParser:

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        self.namespaces = {
            'arxiv': 'http://arxiv.org/OAI/arXiv/',
            'oai':'http://www.openarchives.org/OAI/2.0/'
        }

    def parse_xml(self, xml_content: bytes):
        papers = []

        root = ET.fromstring(xml_content)

        for record in root.findall('.//oai:record', self.namespaces):
            header = record.find('oai:header', self.namespaces)
            if header is not None and header.get('status') == 'deleted':
                continue

            identifier = header.findtext('oai:identifier', default='', namespaces=self.namespaces)
            datestamp = header.findtext('oai:datestamp', default='', namespaces=self.namespaces)

            if not identifier or not datestamp:
                self.logger.warning("Missing identifier or datestamp for record, skipping.")
                continue

            metadata = record.find('oai:metadata', self.namespaces)
            if metadata is None:
                self.logger.warning("No metadata found for record with identifier: %s", identifier)
                continue

            arxiv_metadata = metadata.find('arxiv:arXiv', self.namespaces)
            if arxiv_metadata is not None:
                title = arxiv_metadata.find('arxiv:title', self.namespaces).text
                abstract = arxiv_metadata.find('arxiv:abstract', self.namespaces).text
                paper = Paper(identifier, datestamp, title, abstract)
                papers.append(paper)

        return papers


    def parse_xml_resumables(
            self, xml_bytes: bytes
        ) -> tuple[str | None, datetime.datetime | None, str | None, str | None]:
    
        new_resume_token = None
        expiration_date = None
        last_datestamp = None
    
        parser = ET.fromstring(xml_bytes)
    
        error_elem = parser.find("./oai:error", namespaces=self.namespaces)
        if error_elem is not None:
            code = error_elem.get("code", "unknown")
            raise ValueError(f"OAI-PMH error {code}: {error_elem.text or ''}")
    
        resumeToken_elem = parser.find(".//oai:resumptionToken", namespaces=self.namespaces)
            
        if resumeToken_elem is not None:
            new_resume_token = resumeToken_elem.text or None
            expiration_date = resumeToken_elem.get("expirationDate")
            expiration_date = (
                datetime.datetime.fromisoformat(expiration_date)
                if expiration_date is not None else None
            )
            
        headers = parser.findall(
            "./oai:ListRecords/oai:record/oai:header", namespaces=self.namespaces
        )
        if headers:
            last_datestamp = headers[-1].findtext("oai:datestamp", namespaces=self.namespaces)
            
        return new_resume_token, expiration_date, last_datestamp, None