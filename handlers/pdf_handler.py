"""PDF handler — PDF→DOCX for editing, fpdf2 for fresh creation, PyPDF2 for reading.

Architecture:
- CREATE: Generate fresh PDF from content via fpdf2
- READ: Extract text directly via PyPDF2 (fast, no conversion needed)
- UPDATE: Convert PDF→DOCX, edit in DOCX, convert back to PDF
  ⚠️ This is a LOSSY operation — images, forms, annotations, signatures,
  and complex layout may be lost. The server warns about this.
- The DOCX intermediary preserves structure (headings, lists, tables) that
  raw text extraction loses.
"""

import os
import shutil
import tempfile
from .safe_io import check_file_size, validate_line_range


class PdfHandler:
    """Handles .pdf via DOCX intermediary for edits, fpdf2 for creation."""

    def create(self, path: str, content: str, **kwargs) -> str:
        from fpdf import FPDF

        title = kwargs.get("title")

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)

        if title:
            pdf.set_font("Helvetica", "B", size=16)
            pdf.multi_cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(5)
            pdf.set_font("Helvetica", size=11)

        for line in content.split("\n"):
            if line.startswith("# "):
                pdf.set_font("Helvetica", "B", size=14)
                pdf.multi_cell(0, 8, line[2:], new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=11)
            elif line.startswith("## "):
                pdf.set_font("Helvetica", "B", size=12)
                pdf.multi_cell(0, 7, line[3:], new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=11)
            elif line.strip() == "":
                pdf.ln(3)
            else:
                pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")

        pdf.output(path)
        pages = pdf.page
        return f"Created: {path} (.pdf, {pages} page(s))"

    def read(self, path: str, start_line: int = None, end_line: int = None) -> str:
        from PyPDF2 import PdfReader

        check_file_size(path)
        reader = PdfReader(path)
        lines = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                lines.append(f"--- Page {i+1} ---")
                lines.extend(text.split("\n"))

        total = len(lines)
        start, end = validate_line_range(start_line, end_line, total)

        if start_line or end_line:
            lines = lines[start:end]

        numbered = []
        base = start + 1
        for i, line in enumerate(lines):
            numbered.append(f"{base + i}. {line}")

        return "\n".join(numbered)

    def update(self, path: str, mode: str, content: str, find: str = None) -> str:
        """Edit PDF by converting to DOCX, applying the edit, and converting back.

        ⚠️ LOSSY OPERATION: Images, forms, annotations, signatures, bookmarks,
        accessibility tags, and complex layout WILL be lost. Only text structure
        (headings, paragraphs, tables, lists) is preserved.

        Flow: PDF → temp.docx → edit via DocxHandler → temp.docx → PDF
        """
        from .docx_handler import DocxHandler

        check_file_size(path)
        docx_handler = DocxHandler()

        # Use TemporaryDirectory for safe cleanup
        with tempfile.TemporaryDirectory(prefix="crudoc_pdf_") as tmp_dir:
            tmp_docx = os.path.join(tmp_dir, "edit.docx")
            tmp_pdf = os.path.join(tmp_dir, "output.pdf")

            # Step 1: Convert PDF → DOCX
            self._pdf_to_docx(path, tmp_docx)

            # Step 2: Apply the edit via DocxHandler
            result = docx_handler.update(tmp_docx, mode=mode, content=content, find=find)

            # Check if edit succeeded
            if result.startswith("Error:"):
                return result

            # Step 3: Convert edited DOCX → temp PDF (not direct to original)
            self._docx_to_pdf(tmp_docx, tmp_pdf)

            # Step 4: Atomic replace — only overwrite original after success
            shutil.copy2(tmp_pdf, path)

        warning = (
            "\n⚠️ NOTE: PDF was edited via DOCX conversion. "
            "Images, forms, annotations, and signatures may have been lost."
        )
        return f"Updated: {path} (mode: {mode}, edited via DOCX intermediary){warning}"

    def info(self, path: str) -> dict:
        from PyPDF2 import PdfReader

        check_file_size(path)
        reader = PdfReader(path)
        total_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                total_text += text

        return {
            "pages": len(reader.pages),
            "words": len(total_text.split()),
            "characters": len(total_text),
            "format": "pdf"
        }

    def _pdf_to_docx(self, pdf_path: str, docx_path: str):
        """Convert PDF to DOCX using pdf2docx (preserves structure)."""
        import logging
        from pdf2docx import Converter

        # Suppress pdf2docx INFO logging
        logging.getLogger("pdf2docx").setLevel(logging.WARNING)

        cv = Converter(pdf_path)
        try:
            cv.convert(docx_path, start=0, end=None)
        finally:
            cv.close()

    def _docx_to_pdf(self, docx_path: str, pdf_path: str):
        """Convert DOCX back to PDF using fpdf2 with structural awareness."""
        from docx import Document
        from fpdf import FPDF

        doc = Document(docx_path)

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)

        for para in doc.paragraphs:
            text = para.text
            if not text.strip():
                pdf.ln(3)
                continue

            style_name = para.style.name if para.style else ""

            if "Heading 1" in style_name or style_name == "Title":
                pdf.set_font("Helvetica", "B", size=16)
                pdf.multi_cell(0, 10, text, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=11)
            elif "Heading 2" in style_name:
                pdf.set_font("Helvetica", "B", size=14)
                pdf.multi_cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=11)
            elif "Heading 3" in style_name:
                pdf.set_font("Helvetica", "B", size=12)
                pdf.multi_cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", size=11)
            elif "List" in style_name:
                pdf.multi_cell(0, 5, f"  • {text}", new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.multi_cell(0, 5, text, new_x="LMARGIN", new_y="NEXT")

        # Render tables
        for table in doc.tables:
            pdf.ln(3)
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells)
                pdf.set_font("Helvetica", size=9)
                pdf.multi_cell(0, 4, row_text, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", size=11)
            pdf.ln(3)

        pdf.output(pdf_path)

