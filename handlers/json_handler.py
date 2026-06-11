"""JSON handler with structure-aware operations."""

import json
import os
from .safe_io import atomic_write, check_file_size, validate_line_range


class JsonHandler:
    """Handles .json files with JSON-aware validation and merge."""

    def create(self, path: str, content: str, **kwargs) -> str:
        try:
            data = json.loads(content)
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            formatted = content

        atomic_write(path, formatted)
        return f"Created: {path} (.json, {len(formatted)} chars)"

    def read(self, path: str, start_line: int = None, end_line: int = None) -> str:
        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total = len(lines)
        start, end = validate_line_range(start_line, end_line, total)

        if start_line or end_line:
            lines = lines[start:end]

        numbered = []
        base = start + 1
        for i, line in enumerate(lines):
            numbered.append(f"{base + i}. {line.rstrip()}")

        return "\n".join(numbered)

    def update(self, path: str, mode: str, content: str, find: str = None) -> str:
        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()

        if mode == "replace":
            new_content = content
        elif mode == "append":
            try:
                orig_data = json.loads(original)
                new_data = json.loads(content)
                if isinstance(orig_data, dict) and isinstance(new_data, dict):
                    orig_data.update(new_data)
                    new_content = json.dumps(orig_data, indent=2, ensure_ascii=False)
                elif isinstance(orig_data, list) and isinstance(new_data, list):
                    orig_data.extend(new_data)
                    new_content = json.dumps(orig_data, indent=2, ensure_ascii=False)
                else:
                    new_content = original.rstrip() + "\n" + content
            except json.JSONDecodeError:
                new_content = original.rstrip() + "\n" + content
        elif mode == "prepend":
            new_content = content + "\n" + original
        elif mode == "patch":
            if find not in original:
                return f"Error: Could not find the specified text in {path}"
            count = original.count(find)
            if count > 1:
                return f"Error: Found {count} occurrences. Patch requires exactly 1 match."
            new_content = original.replace(find, content, 1)
        else:
            return f"Error: Unknown mode '{mode}'"

        # Validate resulting JSON — warn but don't block
        warning = ""
        try:
            json.loads(new_content)
        except json.JSONDecodeError as e:
            warning = f"\n⚠️ WARNING: Result is not valid JSON: {e}"

        atomic_write(path, new_content)
        return f"Updated: {path} (mode: {mode}){warning}"

    def info(self, path: str) -> dict:
        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                top_keys = list(data.keys())[:20]
                item_count = len(data)
            elif isinstance(data, list):
                top_keys = []
                item_count = len(data)
            else:
                top_keys = []
                item_count = 1
            valid = True
        except json.JSONDecodeError:
            top_keys = []
            item_count = 0
            valid = False

        return {
            "lines": len(lines),
            "characters": len(content),
            "valid_json": valid,
            "item_count": item_count,
            "top_level_keys": top_keys,
            "format": "json"
        }

