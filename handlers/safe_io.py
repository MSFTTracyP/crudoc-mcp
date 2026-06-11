"""Safe I/O utilities for CRUDoc — path validation, atomic writes, and limits.

Centralizes security-critical operations to avoid duplicating logic across handlers.
"""

import os
import uuid
import tempfile
import datetime
from pathlib import Path
from typing import Optional


# ─── Configuration via environment ───────────────────────────────────────────

# Comma-separated list of allowed root directories. Defaults to user home.
_allowed_roots_raw = os.environ.get(
    "CRUDOC_ALLOWED_ROOTS",
    os.path.expanduser("~")
)
ALLOWED_ROOTS = [
    Path(r.strip()).resolve()
    for r in _allowed_roots_raw.split(",")
    if r.strip()
]

# Maximum file size for read/write operations (bytes). Default 50 MB.
MAX_FILE_SIZE = int(os.environ.get("CRUDOC_MAX_FILE_SIZE", 50 * 1024 * 1024))

# Maximum number of files returned by doc_list
MAX_LIST_RESULTS = int(os.environ.get("CRUDOC_MAX_LIST_RESULTS", 5000))

# Maximum directory recursion depth
MAX_RECURSION_DEPTH = int(os.environ.get("CRUDOC_MAX_RECURSION_DEPTH", 10))

# Archive directory default
ARCHIVE_DIR_DEFAULT = os.path.expanduser("~/.crudoc/archive")


# ─── Path Validation ─────────────────────────────────────────────────────────

class PathValidationError(Exception):
    """Raised when a path fails security validation."""
    pass


def validate_path(path: str, must_exist: bool = False, must_not_exist: bool = False,
                  must_be_file: bool = False, must_be_dir: bool = False) -> Path:
    """Validate and resolve a path against security constraints.

    Returns the resolved Path on success.
    Raises PathValidationError on any violation.
    """
    if not path or not path.strip():
        raise PathValidationError("Path cannot be empty.")

    p = Path(path)

    # Must be absolute
    if not p.is_absolute():
        raise PathValidationError(
            f"Path must be absolute. Got relative path: {path}"
        )

    # Resolve to canonical path (resolves symlinks, .., etc.)
    try:
        resolved = p.resolve(strict=False)
    except (OSError, ValueError) as e:
        raise PathValidationError(f"Cannot resolve path: {path} ({e})")

    # Check against allowed roots
    if not any(
        resolved == root or _is_subpath(resolved, root)
        for root in ALLOWED_ROOTS
    ):
        raise PathValidationError(
            f"Path is outside allowed directories. "
            f"Allowed roots: {[str(r) for r in ALLOWED_ROOTS]}"
        )

    # Existence checks
    if must_exist and not resolved.exists():
        raise PathValidationError(f"File not found: {path}")

    if must_not_exist and resolved.exists():
        raise PathValidationError(
            f"File already exists: {path}\nUse doc_update to modify existing files."
        )

    if must_be_file and not resolved.is_file():
        raise PathValidationError(
            f"Path is not a regular file: {path}"
        )

    if must_be_dir and not resolved.is_dir():
        raise PathValidationError(
            f"Path is not a directory: {path}"
        )

    # Reject paths that are symlinks pointing outside allowed roots
    if resolved.exists() and p.is_symlink():
        link_target = p.resolve(strict=True)
        if not any(_is_subpath(link_target, root) for root in ALLOWED_ROOTS):
            raise PathValidationError(
                f"Symlink target is outside allowed directories: {path} → {link_target}"
            )

    return resolved


def _is_subpath(child: Path, parent: Path) -> bool:
    """Check if child is a subpath of parent (both must be resolved)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# ─── Atomic File Operations ──────────────────────────────────────────────────

def atomic_write(path: str, content: str, encoding: str = "utf-8") -> None:
    """Write content to a file atomically (write temp → fsync → replace).

    Ensures the file is never left in a partial state.
    """
    resolved = Path(path)
    parent = resolved.parent

    # Write to temp file in the same directory (ensures same filesystem for rename)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".crudoc_tmp_",
        suffix=resolved.suffix,
        dir=str(parent)
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(resolved))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Write bytes to a file atomically."""
    resolved = Path(path)
    parent = resolved.parent

    fd, tmp_path = tempfile.mkstemp(
        prefix=".crudoc_tmp_",
        suffix=resolved.suffix,
        dir=str(parent)
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(resolved))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── Backup Utilities ────────────────────────────────────────────────────────

def create_backup(path: str) -> str:
    """Create a timestamped backup of a file. Returns the backup path."""
    import shutil
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    backup_path = f"{path}.bak.{timestamp}.{short_id}"
    shutil.copy2(path, backup_path)
    return backup_path


# ─── File Size Check ─────────────────────────────────────────────────────────

def check_file_size(path: str) -> None:
    """Raise PathValidationError if file exceeds MAX_FILE_SIZE."""
    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        size_mb = round(size / (1024 * 1024), 1)
        limit_mb = round(MAX_FILE_SIZE / (1024 * 1024), 1)
        raise PathValidationError(
            f"File too large: {size_mb} MB (limit: {limit_mb} MB). "
            f"Set CRUDOC_MAX_FILE_SIZE to increase."
        )


# ─── Line Range Validation ───────────────────────────────────────────────────

def validate_line_range(start_line: Optional[int], end_line: Optional[int], total_lines: int) -> tuple:
    """Validate and normalize line range parameters.

    Returns (start_0based, end_exclusive) tuple.
    """
    if start_line is not None and start_line < 1:
        raise PathValidationError(f"start_line must be >= 1, got {start_line}")
    if end_line is not None and end_line < 1:
        raise PathValidationError(f"end_line must be >= 1, got {end_line}")
    if start_line and end_line and end_line < start_line:
        raise PathValidationError(
            f"end_line ({end_line}) must be >= start_line ({start_line})"
        )

    start = (start_line or 1) - 1
    end = min(end_line or total_lines, total_lines)
    return start, end
