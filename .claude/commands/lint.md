Run a full lint and type-check pass on the codebase and fix every issue found.

## Step 1 — Syntax check
```
python -m py_compile main.py services/*.py tests/*.py
```
Report any syntax errors and fix them before continuing.

## Step 2 — Type checking with Pyright
```
pyright main.py services/ tests/
```
If pyright is not installed: `pip install pyright` (do not add it to requirements.txt).

For each reported error:
- `reportOptionalMemberAccess` — the variable can be None; add a `is None` guard or use `or` fallback
- `reportMissingImports` — package not in `.venv`; check requirements.txt / requirements-dev.txt
- `reportReturnType` — function return type doesn't match; fix the return or the annotation
- `reportArgumentType` — wrong type passed; fix at the call site, not by casting

## Step 3 — Tests
```
pytest
```
Every test must pass. They run offline (local storage, fake bank).

## Step 4 — Check for common issues specific to this project

**openpyxl**: `wb.active` returns `Worksheet | None` — always guard with `if ws is None: raise ValueError(...)`.

**FastAPI UploadFile**: `file.filename` is `str | None` — use `(file.filename or "")`.

**os.environ**: `os.environ["KEY"]` raises KeyError if missing; `os.environ.get("KEY")` returns `None`. Use `[]` only for required vars (they'll fail loudly at startup, which is correct). Use `.get()` only for optional ones.

**Jinja2 TemplateResponse**: use `templates.TemplateResponse(request, name, context)`; pages go through `_render()` so the nav gets `review_count` and `bank`.

**Storage**: never write S3 JSON directly from routes; use `repo.update_ledger/update_rules/update_settings` so conflicting writes retry.

## Step 5 — Verify no secrets in tracked files
```
git ls-files | xargs grep -l "AKIA" 2>/dev/null
git ls-files | xargs grep -l "BEGIN PRIVATE KEY" 2>/dev/null
```
If anything other than `.env.example` (AKIA placeholder) or the test key helpers is returned, stop immediately and alert the user.
Check that `.env.example` only contains placeholder values (AKIA followed by X's, not real characters).

## Step 6 — Report
List every file changed, what was wrong, and what the fix was.
Then ask: "Run /push to commit these fixes?"
