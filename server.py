"""
===============================================================================
CRUDoc MCP Server — Document CRUD Operations
Author: tracyp
===============================================================================

CRUDoc MCP Server — Document CRUD Operations

Provides MCP tools for Creating, Reading, Updating, and Deleting documents.
Supports: .md, .txt, .yaml, .json, .docx, .pdf, and all text-based formats.

Security features:
- Path sandboxing: All paths validated against CRUDOC_ALLOWED_ROOTS
- Atomic writes: Files are never left in a partial state
- Sensitivity labels: Fail-closed detection blocks destructive ops on error
- Confirmation gates: Archive/delete require explicit confirmation
- File size limits: Configurable via CRUDOC_MAX_FILE_SIZE

Destructive operations (archive, delete) require explicit confirmation.
"""

import asyncio
import json
import os
import uuid
import datetime
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Document handlers
from handlers.text_handler import TextHandler
from handlers.docx_handler import DocxHandler
from handlers.pdf_handler import PdfHandler
from handlers.yaml_handler import YamlHandler
from handlers.json_handler import JsonHandler
from handlers.label_detector import detect_label_safe, check_operation_allowed, DetectionStatus
from handlers.safe_io import (
    validate_path, PathValidationError, create_backup,
    ARCHIVE_DIR_DEFAULT, MAX_LIST_RESULTS, MAX_RECURSION_DEPTH
)

app = Server("crudoc")

# Registry of format handlers
HANDLERS = {
    ".md": TextHandler(),
    ".txt": TextHandler(),
    ".log": TextHandler(),
    ".csv": TextHandler(),
    ".tsv": TextHandler(),
    ".xml": TextHandler(),
    ".html": TextHandler(),
    ".htm": TextHandler(),
    ".rst": TextHandler(),
    ".adoc": TextHandler(),
    ".tex": TextHandler(),
    ".yaml": YamlHandler(),
    ".yml": YamlHandler(),
    ".json": JsonHandler(),
    ".docx": DocxHandler(),
    ".pdf": PdfHandler(),
}

# Archive directory (configurable via env)
ARCHIVE_DIR = os.environ.get("CRUDOC_ARCHIVE_DIR", os.path.expanduser("~/.crudoc/archive"))


def get_handler(path: str):
    """Get the appropriate handler for a file extension."""
    ext = Path(path).suffix.lower()
    handler = HANDLERS.get(ext)
    if not handler:
        # Fall back to text handler for unknown extensions
        return TextHandler()
    return handler


def ensure_archive_dir():
    """Create the archive directory if it doesn't exist."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="doc_create",
            description="Create a new document. Supports .md, .txt, .yaml, .json, .docx, .pdf and all text formats. Fails if file already exists (use doc_update for existing files).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute path for the new document"
                    },
                    "content": {
                        "type": "string",
                        "description": "Document content (markdown/text for .docx; text for others)"
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title (used for .docx and .pdf metadata)"
                    }
                },
                "required": ["path", "content"]
            }
        ),
        Tool(
            name="doc_read",
            description="Read a document's content. Returns text for all formats including .docx and .pdf (extracted text).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute path to the document"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional: start reading from this line (1-based)"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional: stop reading at this line (inclusive)"
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="doc_update",
            description="Update an existing document. Modes: 'replace' (full content replacement), 'patch' (find and replace a string — preserves formatting on untouched runs), 'append' (add to end), 'prepend' (insert at start — preserves original formatting). Creates a backup before modifying. Sensitivity labels are preserved across all modes. PDF operations edit via DOCX intermediary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute path to the document"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "patch", "append", "prepend"],
                        "description": "Update mode: replace (full), patch (find/replace), append, prepend"
                    },
                    "content": {
                        "type": "string",
                        "description": "New content (for replace/append/prepend) or replacement text (for patch)"
                    },
                    "find": {
                        "type": "string",
                        "description": "Text to find (required for patch mode)"
                    },
                    "backup": {
                        "type": "boolean",
                        "description": "Create a .bak backup before modifying (default: true)"
                    }
                },
                "required": ["path", "mode", "content"]
            }
        ),
        Tool(
            name="doc_archive",
            description="Move a document to the archive directory (~/.crudoc/archive/) with a timestamp. REQUIRES CONFIRMATION: the caller must include 'confirm: true' to proceed. This is a destructive operation — the file is moved, not copied.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute path to the document to archive"
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to proceed. Set after user confirms."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for archiving (logged in manifest). REQUIRED for sensitivity-labeled documents."
                    }
                },
                "required": ["path", "confirm"]
            }
        ),
        Tool(
            name="doc_delete",
            description="Permanently delete a document. REQUIRES CONFIRMATION: the caller must include 'confirm: true' to proceed. This is irreversible. Consider doc_archive instead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute path to the document to delete"
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to proceed. Set after user confirms."
                    }
                },
                "required": ["path", "confirm"]
            }
        ),
        Tool(
            name="doc_list",
            description="List documents in a directory. Optionally filter by extension. Returns file names, sizes, and modification dates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory path to list"
                    },
                    "extension": {
                        "type": "string",
                        "description": "Optional: filter by extension (e.g., '.md', '.docx')"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Search subdirectories (default: false)"
                    }
                },
                "required": ["directory"]
            }
        ),
        Tool(
            name="doc_info",
            description="Get document metadata: size, line count, word count, modification date, format details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute path to the document"
                    }
                },
                "required": ["path"]
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "doc_create":
            return await handle_create(arguments)
        elif name == "doc_read":
            return await handle_read(arguments)
        elif name == "doc_update":
            return await handle_update(arguments)
        elif name == "doc_archive":
            return await handle_archive(arguments)
        elif name == "doc_delete":
            return await handle_delete(arguments)
        elif name == "doc_list":
            return await handle_list(arguments)
        elif name == "doc_info":
            return await handle_info(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except PathValidationError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {str(e)}")]


async def handle_create(args: dict):
    path = args["path"]
    content = args["content"]
    title = args.get("title")

    # Validate: must be absolute, within allowed roots, must NOT exist
    resolved = validate_path(path, must_not_exist=True)

    # Ensure parent directory exists
    parent = resolved.parent
    parent.mkdir(parents=True, exist_ok=True)

    handler = get_handler(path)
    result = handler.create(str(resolved), content, title=title)

    # VERIFY — confirm file was actually created
    if not resolved.exists():
        return [TextContent(type="text", text=f"Error: Create operation completed but file not found at: {path}")]

    return [TextContent(type="text", text=result)]


async def handle_read(args: dict):
    path = args["path"]
    start_line = args.get("start_line")
    end_line = args.get("end_line")

    # Validate: must exist and be a file
    resolved = validate_path(path, must_exist=True, must_be_file=True)

    handler = get_handler(path)
    result = handler.read(str(resolved), start_line=start_line, end_line=end_line)
    return [TextContent(type="text", text=result)]


async def handle_update(args: dict):
    path = args["path"]
    mode = args["mode"]
    content = args["content"]
    find = args.get("find")
    backup = args.get("backup", True)

    # Validate: must exist and be a file
    resolved = validate_path(path, must_exist=True, must_be_file=True)
    resolved_str = str(resolved)

    if mode == "patch" and not find:
        return [TextContent(type="text", text="Error: 'find' parameter is required for patch mode.")]

    # Check sensitivity label (fail-closed)
    detection = detect_label_safe(resolved_str)
    allowed, msg = check_operation_allowed(
        detection.label, "update", detection_result=detection
    )
    if not allowed:
        return [TextContent(type="text", text=msg)]

    # Create timestamped backup
    if backup:
        backup_path = create_backup(resolved_str)

    handler = get_handler(path)
    result = handler.update(resolved_str, mode=mode, content=content, find=find)

    # Append label warning if present
    if msg:
        result = f"{result}\n{msg}"

    return [TextContent(type="text", text=result)]


async def handle_archive(args: dict):
    path = args["path"]
    confirm = args.get("confirm", False)
    reason = args.get("reason", "No reason provided")

    if not confirm:
        return [TextContent(type="text", text="⚠️ CONFIRMATION REQUIRED: This will MOVE the file to the archive.\nAsk the user to confirm, then call again with confirm: true.")]

    # Validate: must exist and be a FILE (not directory)
    resolved = validate_path(path, must_exist=True, must_be_file=True)
    resolved_str = str(resolved)

    # Check sensitivity label (fail-closed)
    detection = detect_label_safe(resolved_str)
    allowed, msg = check_operation_allowed(
        detection.label, "archive", confirm=confirm, reason=reason,
        detection_result=detection
    )
    if not allowed:
        return [TextContent(type="text", text=msg)]

    ensure_archive_dir()

    # Archive with timestamp + random suffix to prevent collisions
    filename = resolved.name
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    archive_name = f"{timestamp}_{short_id}_{filename}"
    archive_path = os.path.join(ARCHIVE_DIR, archive_name)

    # Fail if archive target somehow exists (shouldn't with UUID, but be safe)
    if os.path.exists(archive_path):
        return [TextContent(type="text", text=f"Error: Archive target collision: {archive_path}")]

    import shutil
    shutil.move(resolved_str, archive_path)

    # Log to manifest as JSON Lines (prevents injection)
    manifest_path = os.path.join(ARCHIVE_DIR, "manifest.jsonl")
    log_entry = {
        "timestamp": timestamp,
        "source": resolved_str,
        "archive": archive_path,
        "reason": reason,
        "label": detection.label.display_name if detection.label else None,
    }
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    result = f"Archived: {path}\n→ {archive_path}\nReason: {reason}"
    if msg:
        result = f"{result}\n{msg}"
    return [TextContent(type="text", text=result)]


async def handle_delete(args: dict):
    path = args["path"]
    confirm = args.get("confirm", False)

    if not confirm:
        return [TextContent(type="text", text="⚠️ CONFIRMATION REQUIRED: This will PERMANENTLY DELETE the file.\nConsider using doc_archive instead.\nAsk the user to confirm, then call again with confirm: true.")]

    # Validate: must exist and be a FILE (not directory)
    resolved = validate_path(path, must_exist=True, must_be_file=True)
    resolved_str = str(resolved)

    # Check sensitivity label (fail-closed)
    detection = detect_label_safe(resolved_str)
    allowed, msg = check_operation_allowed(
        detection.label, "delete", confirm=confirm,
        detection_result=detection
    )
    if not allowed:
        return [TextContent(type="text", text=msg)]

    os.remove(resolved_str)

    # VERIFY — confirm file was actually removed
    if os.path.exists(resolved_str):
        return [TextContent(type="text", text=f"Error: Delete called but file still exists: {path}")]

    return [TextContent(type="text", text=f"Deleted: {path}")]


async def handle_list(args: dict):
    directory = args["directory"]
    extension = args.get("extension")
    recursive = args.get("recursive", False)

    # Validate: must be an existing directory within allowed roots
    resolved = validate_path(directory, must_exist=True, must_be_dir=True)
    resolved_str = str(resolved)

    # Normalize extension to include leading dot
    if extension and not extension.startswith("."):
        extension = "." + extension

    results = []
    count = 0

    if recursive:
        for root, dirs, files in os.walk(resolved_str):
            # Enforce recursion depth limit
            depth = root.replace(resolved_str, "").count(os.sep)
            if depth > MAX_RECURSION_DEPTH:
                dirs.clear()
                continue

            for f in sorted(files):
                if count >= MAX_LIST_RESULTS:
                    results.append(f"\n... truncated at {MAX_LIST_RESULTS} results")
                    break
                fp = os.path.join(root, f)
                if extension and not f.lower().endswith(extension.lower()):
                    continue
                try:
                    stat = os.stat(fp)
                    modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    size_kb = round(stat.st_size / 1024, 1)
                    results.append(f"{fp} | {size_kb} KB | {modified}")
                    count += 1
                except OSError:
                    continue
            if count >= MAX_LIST_RESULTS:
                break
    else:
        for f in sorted(os.listdir(resolved_str)):
            if count >= MAX_LIST_RESULTS:
                results.append(f"\n... truncated at {MAX_LIST_RESULTS} results")
                break
            fp = os.path.join(resolved_str, f)
            if not os.path.isfile(fp):
                continue
            if extension and not f.lower().endswith(extension.lower()):
                continue
            try:
                stat = os.stat(fp)
                modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                size_kb = round(stat.st_size / 1024, 1)
                results.append(f"{f} | {size_kb} KB | {modified}")
                count += 1
            except OSError:
                continue

    if not results:
        return [TextContent(type="text", text="No matching files found.")]

    header = f"Documents in {directory}" + (f" (*.{extension})" if extension else "")
    return [TextContent(type="text", text=f"{header}\n\n" + "\n".join(results))]


async def handle_info(args: dict):
    path = args["path"]

    # Validate: must exist and be a file
    resolved = validate_path(path, must_exist=True, must_be_file=True)
    resolved_str = str(resolved)

    stat = os.stat(resolved_str)
    handler = get_handler(path)
    info = handler.info(resolved_str)

    base_info = {
        "path": resolved_str,
        "filename": resolved.name,
        "extension": resolved.suffix.lower(),
        "size_bytes": stat.st_size,
        "size_kb": round(stat.st_size / 1024, 1),
        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created": datetime.datetime.fromtimestamp(stat.st_ctime).isoformat(),
    }
    base_info.update(info)

    # Detect and report sensitivity label
    detection = detect_label_safe(resolved_str)
    if detection.label:
        base_info["sensitivity_label"] = detection.label.to_dict()
    elif detection.status == DetectionStatus.DETECTION_ERROR:
        base_info["sensitivity_label"] = {"error": detection.error_message}
    else:
        base_info["sensitivity_label"] = None

    return [TextContent(type="text", text=json.dumps(base_info, indent=2))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
