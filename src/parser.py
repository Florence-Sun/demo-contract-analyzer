"""
Multi-Modal PDF Contract Ingestion & Preprocessing Pipeline.

Extracts and normalizes three distinct content modalities from uploaded PDF contracts:
1. Hierarchical text clauses and exhibits (segmented via regex header detection).
2. Structured tables converted into Markdown representation (`pdfplumber`).
3. Embedded visual/image metadata (`PyMuPDF`) for architectural diagrams or scanned seals.
"""

import io
import re
from typing import Any, Dict, List
import fitz  # PyMuPDF
import pdfplumber
from src.logger import StructuredTracer


class ContractPDFParser:
    """
    Parses raw PDF byte streams into addressable, metadata-enriched document chunks
    for downstream consumption by the Router and Extractor Agents.

    Attributes:
        tracer (StructuredTracer): Request-scoped structured logger for observability.
    """

    def __init__(self, tracer: StructuredTracer) -> None:
        """
        Initializes the parser with an active structured tracer.

        Args:
            tracer (StructuredTracer): Structured logger instance holding the request `trace_id`.
        """
        self.tracer: StructuredTracer = tracer

    def parse_pdf(self, pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
        """
        Executes multi-modal extraction (text sections, tables, and image metadata) on a PDF.

        Args:
            pdf_bytes (bytes): Raw binary content of the uploaded PDF file.
            filename (str): Original filename of the contract document.

        Returns:
            Dict[str, Any]: Dictionary containing:
                - 'filename' (str): Document name.
                - 'num_pages' (int): Total page count.
                - 'tables_extracted' (int): Count of parsed tables.
                - 'images_extracted' (int): Count of detected embedded images.
                - 'chunks' (List[Dict[str, Any]]): Normalized list of addressable chunks.
        """
        span_id = "sp-ingestion-parser"
        self.tracer.log_event(
            component="IngestionPipeline",
            event_type="PARSE_START",
            message=f"Starting multi-modal PDF parsing for '{filename}'",
            span_id=span_id,
            metadata={"file_size_bytes": len(pdf_bytes)},
        )

        chunks: List[Dict[str, Any]] = []
        tables_extracted: int = 0
        images_extracted: int = 0

        # Pass 1: Extract text sections and embedded image objects via PyMuPDF
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_pages: int = len(pdf_doc)

        for page_idx in range(num_pages):
            page = pdf_doc[page_idx]
            page_num = page_idx + 1
            raw_page_text: str = page.get_text("text")

            # Detect embedded images / scanned exhibits on the current page
            embedded_images = page.get_images(full=True)
            if embedded_images:
                images_extracted += len(embedded_images)
                chunks.append(
                    {
                        "chunk_id": f"P{page_num}-IMG",
                        "page": page_num,
                        "type": "image_metadata",
                        "section_title": f"Page {page_num} Embedded Visual/Diagram Artifact",
                        "content": (
                            f"[Detected {len(embedded_images)} embedded image/diagram object(s) "
                            f"on Page {page_num}. Routed for visual inspection if referenced by clauses.]"
                        ),
                    }
                )

            # Segment page text into logical clauses / sections / exhibits
            page_sections = self._split_into_sections(raw_page_text, page_num)
            chunks.extend(page_sections)

        pdf_doc.close()

        # Pass 2: Extract structured control matrices and summary tables via pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as plumber_pdf:
            for page_idx, page in enumerate(plumber_pdf.pages):
                page_num = page_idx + 1
                extracted_tables = page.extract_tables()
                for tbl_idx, table_matrix in enumerate(extracted_tables):
                    if not table_matrix:
                        continue
                    tables_extracted += 1
                    markdown_table = self._table_to_markdown(table_matrix)
                    chunks.append(
                        {
                            "chunk_id": f"P{page_num}-TBL{tbl_idx + 1}",
                            "page": page_num,
                            "type": "table",
                            "section_title": f"Page {page_num} Structured Control Table {tbl_idx + 1}",
                            "content": markdown_table,
                        }
                    )

        self.tracer.log_event(
            component="IngestionPipeline",
            event_type="PARSE_COMPLETE",
            message=(
                f"Completed parsing '{filename}': {len(chunks)} chunks "
                f"({tables_extracted} tables, {images_extracted} images) across {num_pages} pages."
            ),
            span_id=span_id,
            metadata={
                "num_pages": num_pages,
                "total_chunks": len(chunks),
                "tables_extracted": tables_extracted,
                "images_extracted": images_extracted,
            },
        )

        return {
            "filename": filename,
            "num_pages": num_pages,
            "tables_extracted": tables_extracted,
            "images_extracted": images_extracted,
            "chunks": chunks,
        }

    def _split_into_sections(self, page_text: str, page_num: int) -> List[Dict[str, Any]]:
        """
        Splits raw page text into logical contract clauses based on Section/Exhibit headers.

        Args:
            page_text (str): Raw extracted text for a single PDF page.
            page_num (int): 1-based page number.

        Returns:
            List[Dict[str, Any]]: List of chunk dictionaries with `chunk_id`, `section_title`, and `content`.
        """
        lines = [line.strip() for line in page_text.split("\n") if line.strip()]
        if not lines:
            return []

        section_chunks: List[Dict[str, Any]] = []
        current_title: str = f"Page {page_num} Preamble / General Provisions"
        current_buffer: List[str] = []
        section_counter: int = 1

        # Regex matching standard legal headings: 'Section 4.1', 'Exhibit A', 'Exhibit G13', 'ARTICLE IV'
        header_regex = re.compile(
            r"^(Section\s+\d+(\.\d+)?|Exhibit\s+[A-Z0-9]+|ARTICLE\s+[IVX\d]+)",
            re.IGNORECASE,
        )

        for line in lines:
            if header_regex.match(line):
                if current_buffer:
                    section_chunks.append(
                        {
                            "chunk_id": f"P{page_num}-S{section_counter}",
                            "page": page_num,
                            "type": "text_section",
                            "section_title": current_title,
                            "content": "\n".join(current_buffer),
                        }
                    )
                    section_counter += 1
                    current_buffer = []
                current_title = line
            current_buffer.append(line)

        if current_buffer:
            section_chunks.append(
                {
                    "chunk_id": f"P{page_num}-S{section_counter}",
                    "page": page_num,
                    "type": "text_section",
                    "section_title": current_title,
                    "content": "\n".join(current_buffer),
                }
            )

        return section_chunks

    @staticmethod
    def _table_to_markdown(table_matrix: List[List[Any]]) -> str:
        """
        Converts a 2D list of table cells from `pdfplumber` into a GitHub-flavored Markdown table
        so LLMs can accurately reason over rows and columns.

        Args:
            table_matrix (List[List[Any]]): 2D array of extracted table cells.

        Returns:
            str: Formatted Markdown table string.
        """
        cleaned_rows: List[List[str]] = []
        for row in table_matrix:
            cleaned_rows.append(
                [str(cell).replace("\n", " ").strip() if cell is not None else "" for cell in row]
            )
        if not cleaned_rows:
            return ""

        header_line = "| " + " | ".join(cleaned_rows[0]) + " |"
        separator_line = "| " + " | ".join(["---"] * len(cleaned_rows[0])) + " |"
        body_lines = ["| " + " | ".join(row) + " |" for row in cleaned_rows[1:]]
        return "\n".join([header_line, separator_line] + body_lines)