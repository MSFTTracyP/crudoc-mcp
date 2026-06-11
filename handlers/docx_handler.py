"""DOCX handler using python-docx for Word document operations.

Preserves:
- Sensitivity labels (custom.xml) across all update modes
- Run-level formatting (bold/italic/links) in patch mode
- Original document structure in prepend mode (element insertion)
"""

import os
import zipfile
from .safe_io import check_file_size, validate_line_range


class DocxHandler:
    """Handles .docx Word document format."""

    def create(self, path: str, content: str, **kwargs) -> str:
        from docx import Document

        doc = Document()
        title = kwargs.get("title")

        if title:
            doc.add_heading(title, level=0)

        for line in content.split("\n"):
            if line.startswith("# "):
                doc.add_heading(line[2:], level=1)
            elif line.startswith("## "):
                doc.add_heading(line[3:], level=2)
            elif line.startswith("### "):
                doc.add_heading(line[4:], level=3)
            elif line.startswith("- ") or line.startswith("* "):
                doc.add_paragraph(line[2:], style="List Bullet")
            elif line.strip() == "":
                continue
            else:
                doc.add_paragraph(line)

        doc.save(path)
        para_count = len(doc.paragraphs)
        return f"Created: {path} (.docx, {para_count} paragraphs)"

    def read(self, path: str, start_line: int = None, end_line: int = None) -> str:
        from docx import Document

        check_file_size(path)
        doc = Document(path)
        lines = []
        list_num_counter = 0
        for para in doc.paragraphs:
            text = para.text
            if para.style.name != "List Number":
                list_num_counter = 0
            if para.style.name.startswith("Heading"):
                level = para.style.name.replace("Heading ", "")
                try:
                    prefix = "#" * int(level) + " "
                except ValueError:
                    prefix = "# "
                lines.append(prefix + text)
            elif para.style.name == "List Bullet":
                lines.append(f"- {text}")
            elif para.style.name == "List Number":
                list_num_counter += 1
                lines.append(f"{list_num_counter}. {text}")
            else:
                lines.append(text)

        for table in doc.tables:
            lines.append("")
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                lines.append("| " + " | ".join(cells) + " |")

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
        from docx import Document
        from copy import deepcopy

        check_file_size(path)

        if mode == "replace":
            # Preserve label before replacing
            label_xml = self._extract_custom_xml(path)

            doc = Document()
            for line in content.split("\n"):
                if line.startswith("# "):
                    doc.add_heading(line[2:], level=1)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=2)
                elif line.startswith("### "):
                    doc.add_heading(line[4:], level=3)
                elif line.startswith("- "):
                    doc.add_paragraph(line[2:], style="List Bullet")
                elif line.strip() == "":
                    continue
                else:
                    doc.add_paragraph(line)
            doc.save(path)

            # Restore label
            if label_xml:
                self._restore_custom_xml(path, label_xml)

            # Verify label preservation
            warning = ""
            if label_xml:
                restored = self._extract_custom_xml(path)
                if not restored:
                    warning = "\n⚠️ WARNING: Sensitivity label may not have been preserved correctly."

            return f"Updated: {path} (mode: replace, full content rewritten, label preserved){warning}"

        elif mode == "patch":
            doc = Document(path)

            # Count matches across all paragraphs for ambiguity detection
            match_count = sum(
                1 for para in doc.paragraphs if find in para.text
            )
            if match_count == 0:
                return f"Error: Could not find '{find}' in {path}"
            if match_count > 1:
                return f"Error: Found {match_count} paragraphs containing the search text. Patch requires exactly 1 match."

            found = self._run_aware_replace(doc, find, content)
            if not found:
                return f"Error: Could not find '{find}' in {path}"
            doc.save(path)
            return f"Updated: {path} (mode: patch, text replaced, formatting preserved)"

        elif mode == "append":
            doc = Document(path)
            for line in content.split("\n"):
                if line.strip():
                    doc.add_paragraph(line)
            doc.save(path)
            return f"Updated: {path} (mode: append, content added to end)"

        elif mode == "prepend":
            doc = Document(path)
            body = doc.element.body

            # Build new paragraphs, collect their XML elements
            temp_doc = Document()
            new_elements = []
            for line in content.split("\n"):
                if line.strip():
                    p = temp_doc.add_paragraph(line)
                    new_elements.append(deepcopy(p._element))

            # Insert at the beginning of body in forward order
            for i, elem in enumerate(new_elements):
                body.insert(i, elem)

            doc.save(path)
            return f"Updated: {path} (mode: prepend, content inserted at start, original formatting preserved)"

        return f"Error: Unknown mode '{mode}'"

    def _run_aware_replace(self, doc, find: str, replacement: str) -> bool:
        """Find text across runs and replace while preserving formatting on other runs.

        Strategy: For each paragraph, concatenate run texts to find the match span,
        then modify only the runs that contain the match — preserving formatting
        on all untouched runs.
        """
        for para in doc.paragraphs:
            if find not in para.text:
                continue

            runs = para.runs
            if not runs:
                # No runs (shouldn't happen if text exists, but fallback)
                para.text = para.text.replace(find, replacement, 1)
                return True

            # Build a map of character positions to runs
            full_text = ""
            run_boundaries = []  # (start_idx, end_idx, run_index)
            for i, run in enumerate(runs):
                start = len(full_text)
                full_text += run.text or ""
                run_boundaries.append((start, len(full_text), i))

            match_start = full_text.find(find)
            if match_start == -1:
                continue

            match_end = match_start + len(find)

            # Identify which runs are affected
            affected_runs = []
            for start, end, idx in run_boundaries:
                if end > match_start and start < match_end:
                    affected_runs.append((start, end, idx))

            if not affected_runs:
                continue

            # Single run contains the entire match — simple case
            if len(affected_runs) == 1:
                _, _, idx = affected_runs[0]
                runs[idx].text = runs[idx].text.replace(find, replacement, 1)
                return True

            # Multi-run match: put replacement in first affected run, clear others
            first_start, _, first_idx = affected_runs[0]
            # Text before the match in the first run
            prefix = runs[first_idx].text[:match_start - first_start]
            # Text after the match in the last run
            last_start, _, last_idx = affected_runs[-1]
            suffix = runs[last_idx].text[match_end - last_start:]

            # Set first run to: prefix + replacement + suffix
            runs[first_idx].text = prefix + replacement + suffix

            # Clear intermediate and last affected runs
            for i in range(1, len(affected_runs)):
                _, _, idx = affected_runs[i]
                runs[idx].text = ""

            return True

        return False

    def _extract_custom_xml(self, docx_path: str) -> bytes:
        """Extract docProps/custom.xml from a DOCX (contains MSIP labels)."""
        try:
            with zipfile.ZipFile(docx_path, "r") as z:
                if "docProps/custom.xml" in z.namelist():
                    return z.read("docProps/custom.xml")
        except (zipfile.BadZipFile, KeyError):
            pass
        return None

    def _restore_custom_xml(self, docx_path: str, custom_xml: bytes):
        """Restore docProps/custom.xml into a DOCX file to preserve sensitivity labels."""
        tmp_path = docx_path + ".tmp"
        try:
            with zipfile.ZipFile(docx_path, "r") as z_in:
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z_out:
                    for item in z_in.namelist():
                        if item == "docProps/custom.xml":
                            continue  # Skip — we'll write our version
                        z_out.writestr(item, z_in.read(item))
                    z_out.writestr("docProps/custom.xml", custom_xml)

            # Replace original with updated
            os.replace(tmp_path, docx_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def info(self, path: str) -> dict:
        from docx import Document

        doc = Document(path)
        paragraphs = len(doc.paragraphs)
        tables = len(doc.tables)
        words = sum(len(p.text.split()) for p in doc.paragraphs)
        characters = sum(len(p.text) for p in doc.paragraphs)

        return {
            "paragraphs": paragraphs,
            "tables": tables,
            "words": words,
            "characters": characters,
            "format": "docx"
        }
