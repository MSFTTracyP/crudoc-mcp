# CRUDoc MCP Server

**Document CRUD operations for AI assistants via the Model Context Protocol.**

CRUDoc gives your AI tools the ability to create, read, update, delete, archive, list, and inspect documents across all common formats — with built-in safety features.

## Supported Formats

| Format | Extensions | Features |
|--------|-----------|----------|
| **Plain text** | `.md`, `.txt`, `.log`, `.csv`, `.tsv`, `.xml`, `.html`, `.rst`, `.adoc`, `.tex` | Line-level read, patch, append, prepend |
| **YAML** | `.yaml`, `.yml` | Structure-aware validation, key introspection |
| **JSON** | `.json` | Smart merge on append (dict/list aware), validation |
| **Word** | `.docx` | Heading/list detection, run-aware patch (preserves formatting), sensitivity label preservation |
| **PDF** | `.pdf` | Text extraction, create via fpdf2, update via DOCX intermediary |

## Installation

### GitHub Copilot CLI (plugin)

```bash
copilot plugin install MSFTTracyP/crudoc-mcp
```

That's it. The server loads automatically on next session.

### VS Code

Clone the repo, then add to `.vscode/mcp.json` in your workspace (or user settings):

```bash
git clone https://github.com/MSFTTracyP/crudoc-mcp.git
cd crudoc-mcp
pip install -r requirements.txt
```

`.vscode/mcp.json`:

```json
{
  "servers": {
    "crudoc": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "C:/path/to/crudoc-mcp"
    }
  }
}
```

### Other MCP Clients (Claude Desktop, etc.)

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "crudoc": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "/path/to/crudoc-mcp"
    }
  }
}
```

### Prerequisites

- Python 3.10+
- Install dependencies: `pip install -r requirements.txt`

## Tools (7)

### `doc_create`

Create a new document. Fails if file already exists.

```
Parameters:
  path     (required) — Full absolute path for the new document
  content  (required) — Document content
  title    (optional) — Title metadata (used for .docx and .pdf)
```

**Example:**
```json
{
  "path": "/home/user/docs/meeting-notes.md",
  "content": "# Sprint Planning\n\n- Review backlog\n- Assign stories\n- Set sprint goal"
}
```

---

### `doc_read`

Read a document's content. Returns extracted text for all formats including binary (.docx, .pdf).

```
Parameters:
  path       (required) — Full absolute path to the document
  start_line (optional) — Start reading from this line (1-based)
  end_line   (optional) — Stop reading at this line (inclusive)
```

**Example:**
```json
{
  "path": "/home/user/docs/config.yaml",
  "start_line": 10,
  "end_line": 25
}
```

---

### `doc_update`

Update an existing document with four modes.

```
Parameters:
  path    (required) — Full absolute path to the document
  mode    (required) — One of: replace, patch, append, prepend
  content (required) — New content or replacement text
  find    (required for patch) — Text to find (must have exactly 1 match)
  backup  (optional) — Create timestamped .bak before modifying (default: true)
```

**Modes:**

| Mode | Behavior |
|------|----------|
| `replace` | Full content replacement |
| `patch` | Find exact text and replace it (requires exactly 1 match) |
| `append` | Add content to end (JSON: smart merge for dict/list) |
| `prepend` | Insert content at start (DOCX: preserves original formatting) |

**Example — patch mode:**
```json
{
  "path": "/home/user/docs/readme.md",
  "mode": "patch",
  "find": "version: 1.0.0",
  "content": "version: 1.1.0"
}
```

**Example — JSON append (smart merge):**
```json
{
  "path": "/home/user/config.json",
  "mode": "append",
  "content": "{\"newKey\": \"newValue\"}"
}
```

---

### `doc_archive`

Move a document to the archive directory (`~/.crudoc/archive/`) with timestamp. Requires confirmation.

```
Parameters:
  path    (required) — Full absolute path to the document
  confirm (required) — Must be true to proceed
  reason  (optional) — Reason for archiving (REQUIRED for sensitivity-labeled docs)
```

**Example:**
```json
{
  "path": "/home/user/docs/old-spec.docx",
  "confirm": true,
  "reason": "Superseded by v2 spec"
}
```

Archived files are renamed with timestamp + UUID: `20260611-123000_a1b2c3_old-spec.docx`

---

### `doc_delete`

Permanently delete a document. Irreversible. Requires confirmation.

```
Parameters:
  path    (required) — Full absolute path to the document
  confirm (required) — Must be true to proceed
```

**Example:**
```json
{
  "path": "/home/user/docs/temp-scratch.txt",
  "confirm": true
}
```

> **Note:** Documents with Confidential+ sensitivity labels cannot be deleted. Use `doc_archive` instead.

---

### `doc_list`

List documents in a directory with optional filtering.

```
Parameters:
  directory (required) — Directory path to list
  extension (optional) — Filter by extension (e.g., '.md', '.docx')
  recursive (optional) — Search subdirectories (default: false)
```

**Example:**
```json
{
  "directory": "/home/user/docs",
  "extension": ".md",
  "recursive": true
}
```

Returns: filename, size (KB), and last modified date for each file.

---

### `doc_info`

Get document metadata as JSON.

```
Parameters:
  path (required) — Full absolute path to the document
```

**Returns:**
```json
{
  "path": "/home/user/docs/report.docx",
  "filename": "report.docx",
  "extension": ".docx",
  "size_bytes": 45230,
  "size_kb": 44.2,
  "modified": "2026-06-11T10:30:00",
  "created": "2026-05-01T09:00:00",
  "paragraphs": 42,
  "tables": 3,
  "words": 1580,
  "characters": 8942,
  "format": "docx",
  "sensitivity_label": {
    "label_name": "General",
    "sensitivity_level": 1,
    "is_encrypted": false,
    "is_protected": false
  }
}
```

---

## Security Features

### Path Sandboxing

All file operations are restricted to allowed directories. By default, only your home directory (`~`) is permitted.

Configure via environment variable:
```bash
export CRUDOC_ALLOWED_ROOTS="/home/user/docs,/home/user/projects"
```

Rejected operations:
- Relative paths
- Paths outside allowed roots
- Symlinks pointing outside allowed roots

### Sensitivity Label Detection (MIP)

CRUDoc detects Microsoft Information Protection (MIP) labels on `.docx` and `.pdf` files:

| Label Level | Read | Update | Archive | Delete |
|-------------|------|--------|---------|--------|
| Public / General | ✅ | ✅ | ✅ | ✅ |
| Confidential | ✅ | ✅ (warns) | ✅ (requires reason) | 🚫 Blocked |
| Highly Confidential | ✅ | ✅ (warns) | ✅ (requires reason) | 🚫 Blocked |

**Fail-closed behavior:** If label detection fails (corrupt file, parse error), destructive operations are blocked until the issue is resolved.

### Atomic Writes

All file modifications use write-to-temp → fsync → atomic-replace. Files are never left in a partial or corrupted state, even on crash.

### Timestamped Backups

Updates create backups like `file.md.bak.20260611-123000.a1b2c3` — no overwrites, no collisions.

---

## Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `CRUDOC_ALLOWED_ROOTS` | `~` (home) | Comma-separated allowed directory roots |
| `CRUDOC_ARCHIVE_DIR` | `~/.crudoc/archive` | Where archived files are stored |
| `CRUDOC_MAX_FILE_SIZE` | `52428800` (50 MB) | Maximum file size for read/write operations |
| `CRUDOC_MAX_LIST_RESULTS` | `5000` | Maximum files returned by doc_list |
| `CRUDOC_MAX_RECURSION_DEPTH` | `10` | Maximum directory depth for recursive listing |

---

## Architecture

```
crudoc-mcp/
├── .claude-plugin/
│   └── plugin.json       ← Copilot CLI plugin manifest
├── .mcp.json             ← MCP server definition (VS Code compatible)
├── server.py             ← Main MCP server (tool registration + dispatch)
├── handlers/
│   ├── __init__.py
│   ├── safe_io.py        ← Path validation, atomic writes, limits
│   ├── text_handler.py   ← Plain text formats
│   ├── yaml_handler.py   ← YAML with validation
│   ├── json_handler.py   ← JSON with smart merge
│   ├── docx_handler.py   ← Word docs with label preservation
│   ├── pdf_handler.py    ← PDF create/read/update
│   └── label_detector.py ← MIP sensitivity label detection
├── requirements.txt
├── package.json
└── README.md
```

---

## License

MIT
