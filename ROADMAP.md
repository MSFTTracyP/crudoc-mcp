# CRUDoc Roadmap: Writer-Ready

Goal: make CRUDoc safe and practical for novelists and other writers who work in Word, not just technical users.

**Status key:** ⬜ not started · 🟡 in progress · ✅ done (merged to `master`)

| Step | Item | Branch | Status |
|------|------|--------|--------|
| 0 | Test suite and fixtures | `test/harness` | ⬜ |
| 1 | Guard destructive `replace` (and lossy PDF edits) | `fix/destructive-guard` | ⬜ |
| 2 | Formatting-preserving patch (italics etc.) | `fix/patch-formatting` | ⬜ |
| 3 | Tracked-changes option | `feat/tracked-changes` | ⬜ |
| 4 | Writer-friendly setup (docs + installer) | `docs/writer-setup` | ⬜ |
| 5 | Migrate to `mcp` 2.x | `chore/mcp2` | ⬜ |

Steps 1–3 are the bar for sharing CRUDoc with writing groups.

---

## How we work

- **One branch and one pull request per step.** `master` must stay installable at all times.
- **Every PR passes the checklist below before merge.** No exceptions for "small" changes.
- **Git is run from Windows (PowerShell) only.** Running git on the same checkout from another environment (WSL, a VM, a mounted share) can leave `.git/index.lock` behind and shows false "modified" files because of line-ending differences.
- **Real-document checks use copies.** Never point a test or a manual check at an original manuscript.
- **This file is updated in the same PR** that completes a step (status + any changes to the plan).

### PR checklist

- [ ] `py -m pytest` passes on Windows (no new failures, no unexplained `xfail` changes)
- [ ] New behaviour has tests; fixed bugs have their `xfail` marker removed
- [ ] Any `.docx` output was opened in Word and checked by eye (see the step's manual checks)
- [ ] README and tool descriptions in `server.py` match the new behaviour
- [ ] `CHANGELOG.md` entry added; version bumped in `package.json` and `.claude-plugin/plugin.json`
- [ ] `ROADMAP.md` status updated

---

## Step 0 — Test suite and fixtures

There are no automated tests yet. Every later step depends on being able to prove that behaviour changed only where intended.

**Work**

- Add `tests/` (pytest) and `requirements-dev.txt` (`pytest`).
- Build fixture documents **in code** (python-docx plus raw XML where needed), not as committed binaries, so every fixture is reviewable. Fixtures:
  - a paragraph where the match crosses formatting (`she *never* said`), plus bold, bold+italic, underline and font-colour variants
  - the same text appearing in two paragraphs (ambiguity check)
  - a paragraph containing a hyperlink
  - a table, a header and footer, a footnote
  - curly quotes and apostrophes (`’ “ ”`)
  - a document carrying an MIP sensitivity label (`docProps/custom.xml`)
  - an existing tracked change (`w:ins` / `w:del`)
- `conftest.py` sets `CRUDOC_ALLOWED_ROOTS` to the pytest temp directory **before** importing handlers (roots are read at import time), so tests can never touch real files.
- Baseline tests that **document current behaviour**, with known bugs marked `xfail` and a reason:
  - patch across formatting loses italics (Step 2)
  - `replace` on `.docx` discards all formatting (Step 1)
  - `.docx` writes are not atomic: handlers call `doc.save(path)` directly, although the README promises atomic writes (Step 1)
  - a backup is created even when the patch then fails with 0 or 2+ matches, leaving stray `.bak` files (Step 1)

**Acceptance checks**

- [ ] `py -m pytest` runs green on Windows, with every known bug listed as `xfail` and a reason
- [ ] No test reads or writes outside the pytest temp directory
- [ ] Fixtures open without errors in Word (open each generated file once, by hand)

---

## Step 1 — Guard destructive and lossy edits

`replace` on a `.docx` rebuilds the document from scratch, keeping only `#` headings, `- ` bullets and plain paragraphs. Every PDF edit (any mode) round-trips through DOCX and loses images, forms, annotations and layout. One casual request can wreck a manuscript.

**Work**

- New `doc_update` parameter `confirm_destructive` (boolean, default `false`).
- Refuse `replace` on `.docx`, and **any** update on `.pdf`, unless `confirm_destructive` is `true`. The error message says exactly what would be lost and suggests `patch` instead.
- Create the backup **only after** validation passes, so refused or failed edits leave no `.bak` files.
- Make `.docx` writes atomic: save to a temp file in the same folder, then atomically replace (reuse `safe_io`).
- Update the tool description in `server.py` and the README.

**Acceptance checks**

- [ ] `replace` on `.docx` without the flag → error; file is byte-identical; no `.bak` created
- [ ] Same with the flag → succeeds; `.bak` created; sensitivity label preserved
- [ ] Any `.pdf` update without the flag → refused, file unchanged
- [ ] `replace` on `.md`, `.txt`, `.json`, `.yaml` is unaffected
- [ ] Patch with 0 or 2+ matches → error and **no** `.bak` file
- [ ] The atomic-write test (`xfail` from Step 0) now passes

---

## Step 2 — Formatting-preserving patch

Today, when the text being replaced spans several runs, the whole replacement takes the formatting of the first run, so `she *never* said` → `she *never* once said` loses the italics.

**Rule to implement:** compare `find` and the replacement character by character. Characters that are unchanged at the start and end keep their original run (and formatting). Only the changed middle section is rewritten, and it takes the formatting of the character immediately before it (or the first changed character, if the change is at the start of the paragraph).

**Work**

- Implement the rule in `_run_aware_replace`.
- Handle paragraphs containing hyperlinks without mis-mapping text to runs (python-docx's `paragraph.runs` skips runs inside hyperlinks).
- Keep the "exactly one paragraph must match" rule.

**Acceptance checks**

- [ ] The italics `xfail` from Step 0 now passes, plus bold, bold+italic, underline and colour variants
- [ ] Replacement shorter than, equal to and longer than `find` all keep surrounding formatting
- [ ] A match inside a single run behaves exactly as before
- [ ] Hyperlink paragraph: either patched correctly or refused with a clear error, never corrupted
- [ ] Sensitivity label preserved
- [ ] **Manual:** on a copy of a real chapter, three edits that cross italics look right in Word

---

## Step 3 — Tracked-changes option

Writers working with editors and co-authors expect to review changes in Word and accept or reject them.

**Work**

- New `doc_update` parameters for `.docx` patch: `track_changes` (boolean, default `false`) and `author` (default from `CRUDOC_AUTHOR`, else `"CRUDoc"`).
- Write deletions as `w:del` / `w:delText` and insertions as `w:ins` runs that copy the original formatting, each with a unique `w:id`, `w:author` and `w:date`. Reuse the Step 2 character comparison so only the changed characters are marked.
- **`doc_read` must see tracked text.** python-docx ignores runs inside `w:ins`, so after a tracked edit the inserted text would vanish from `doc_read` and a follow-up patch could not find it. `doc_read` gets a view option (default: the document as it would read with all changes accepted).
- Paragraphs that **already** contain tracked changes: refuse to patch them in v1, with a clear message ("accept or reject existing changes in this paragraph first").

**Acceptance checks**

- [ ] Tracked patch produces valid XML (re-opens with python-docx; the zip is well-formed)
- [ ] `doc_read` shows the accepted view after a tracked edit, and a second patch on the new text works
- [ ] Untracked behaviour is unchanged (all Step 2 tests still pass)
- [ ] A paragraph with an existing tracked change is refused, file unchanged, no `.bak`
- [ ] **Manual (Word):** the Review pane shows each change with the right author and date; Accept All and Reject All both give the expected text and keep the formatting
- [ ] **Manual (LibreOffice):** the same document opens and shows the changes

---

## Step 4 — Writer-friendly setup

Tonight's install needed Python, pip, hand-edited JSON, the Microsoft Store config location, a full path to `python.exe` and a version fix. A writer won't get through that alone.

**Work**

- `install.ps1` (Windows) and `install.sh` (macOS) that:
  - check for Python 3.10+ and say exactly how to install it if missing
  - create a virtual environment in the repo (`.venv`) and install requirements into it (no global packages)
  - find the Claude Desktop config (standard **and** Microsoft Store locations on Windows)
  - print the exact `crudoc` entry with real paths filled in, and offer to merge it into the config **after** making a backup and validating the JSON
- README "For writers" section: plain-language steps, choosing which folders CRUDoc may touch, and troubleshooting (`pip` not recognised, Store config path, JSON comma errors, how to check the server is running).
- Add `.venv/` to `.gitignore`.

**Acceptance checks**

- [ ] Fresh Windows install (Windows Sandbox or a second account) succeeds using only the README
- [ ] Fresh macOS install succeeds using only the README (needs a Mac tester)
- [ ] The installer never overwrites a config without a backup, and refuses to write invalid JSON
- [ ] Running the installer twice is safe (no duplicate entries)

---

## Step 5 — Migrate to `mcp` 2.x

`requirements.txt` pins `mcp<2` because `server.py` uses the 1.x low-level `Server` decorators (`@app.list_tools()`), which 2.x removed.

**Work**

- Port `server.py` to the 2.x API, keeping every tool name, parameter and response format identical.
- Add a test that starts the server in-process and checks the tool list and one call per tool.
- Remove the `<2` pin.

**Acceptance checks**

- [ ] All tests pass on `mcp` 2.x
- [ ] Tool names and schemas identical to before (compared by test)
- [ ] Works in Claude Desktop on Windows after a clean reinstall

---

## Backlog (not scheduled)

- Curly-quote-tolerant matching (`'` matches `’`), so `find` text typed in chat matches manuscript text
- Patch reach beyond body paragraphs: tables, headers and footers, footnotes, text boxes
- Replace-all and "nth occurrence" options for patch
- Dry-run mode that returns a before/after preview without writing
