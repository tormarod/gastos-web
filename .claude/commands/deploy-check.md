Run a pre-deployment checklist before pushing changes to production on Render.

Check each item and report pass / fail / warning:

**Code**
- [ ] All Python files pass syntax check: `python -m py_compile main.py services/*.py tests/*.py`
- [ ] The test suite passes: `pytest`
- [ ] No `.env` file is tracked: `git ls-files .env` should return nothing
- [ ] No real credentials in `.env.example` (look for patterns like `AKIA[A-Z0-9]{16}` with actual values)
- [ ] `__pycache__/`, `*.pyc` and `.data/` are gitignored

**Templates**
- [ ] `dashboard.html` has no hardcoded amounts — all data comes from Jinja2 variables
- [ ] `upload.html` form posts to `/upload` with `enctype="multipart/form-data"` and a `files` input with `multiple`
- [ ] Every page except `login.html` extends `base.html` (navigation, review badge)
- [ ] Statement text inserted from JavaScript goes through `esc()` (no raw `innerHTML` with concepts)

**Render readiness**
- [ ] `render.yaml` has the correct `startCommand`: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- [ ] `requirements.txt` lists all imports used in the codebase (test-only ones go in `requirements-dev.txt`)
- [ ] New environment variables are documented in `.env.example`, `render.yaml`, `README.md` and `DEPLOY.md`

**README.md**
- [ ] Read the current `README.md` and compare it against the actual state of the codebase
- [ ] Check if any new services, routes, or slash commands were added that are not documented
- [ ] Check if the project structure table is still accurate
- [ ] Check if the categorisation rules table reflects what is currently in `services/categorizer.py`
- [ ] If anything is outdated or missing, update `README.md` before pushing — do not ask, just fix it
- [ ] Report exactly what was changed in the README (or "README is up to date" if nothing changed)
- [ ] If anything is outdated or missing, update `CLAUDE.md`. Only the project related descriptions.

**After confirming all pass**, run `/push` to commit and deploy.
If any item fails, fix it first and re-run this checklist.
