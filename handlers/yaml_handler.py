"""YAML handler with structure-aware operations."""

import os
from .safe_io import atomic_write, check_file_size, validate_line_range


class YamlHandler:
    """Handles .yaml and .yml files with YAML-aware validation."""

    def create(self, path: str, content: str, **kwargs) -> str:
        import yaml

        try:
            yaml.safe_load(content)
        except yaml.YAMLError as e:
            pass  # Write anyway — user may want partial YAML

        atomic_write(path, content)
        return f"Created: {path} (.yaml, {len(content)} chars)"

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
        import yaml

        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()

        if mode == "replace":
            new_content = content
        elif mode == "append":
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

        # Validate resulting YAML — warn but don't block
        warning = ""
        try:
            yaml.safe_load(new_content)
        except yaml.YAMLError as e:
            warning = f"\n⚠️ WARNING: Result is not valid YAML: {e}"

        atomic_write(path, new_content)
        return f"Updated: {path} (mode: {mode}){warning}"

    def info(self, path: str) -> dict:
        import yaml

        check_file_size(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")
        try:
            data = yaml.safe_load(content)
            top_keys = list(data.keys()) if isinstance(data, dict) else []
            valid = True
        except Exception:
            top_keys = []
            valid = False

        return {
            "lines": len(lines),
            "characters": len(content),
            "valid_yaml": valid,
            "top_level_keys": top_keys[:20],
            "format": "yaml"
        }

