"""Text handler for plain text formats (.md, .txt, .log, .csv, .xml, .html, etc.)."""

import os
from .safe_io import atomic_write, check_file_size, validate_line_range


class TextHandler:
    """Handles all plain-text document formats."""

    def create(self, path: str, content: str, **kwargs) -> str:
        atomic_write(path, content)
        return f"Created: {path} ({len(content)} chars, {content.count(chr(10)) + 1} lines)"

    def read(self, path: str, start_line: int = None, end_line: int = None) -> str:
        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total = len(lines)
        start, end = validate_line_range(start_line, end_line, total)

        if start_line or end_line:
            lines = lines[start:end]
            prefix = f"[Lines {start+1}-{end}]\n"
        else:
            prefix = ""

        numbered = []
        base = start + 1
        for i, line in enumerate(lines):
            numbered.append(f"{base + i}. {line.rstrip()}")

        return prefix + "\n".join(numbered)

    def update(self, path: str, mode: str, content: str, find: str = None) -> str:
        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()

        if mode == "replace":
            new_content = content
        elif mode == "append":
            new_content = original + content
        elif mode == "prepend":
            new_content = content + original
        elif mode == "patch":
            if find not in original:
                return f"Error: Could not find the specified text in {path}"
            count = original.count(find)
            if count > 1:
                return f"Error: Found {count} occurrences of the search text. Patch requires exactly 1 match."
            new_content = original.replace(find, content, 1)
        else:
            return f"Error: Unknown mode '{mode}'"

        atomic_write(path, new_content)
        return f"Updated: {path} (mode: {mode}, {len(new_content)} chars)"

    def info(self, path: str) -> dict:
        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")
        words = len(content.split())
        return {
            "lines": len(lines),
            "words": words,
            "characters": len(content),
            "format": "text"
        }

