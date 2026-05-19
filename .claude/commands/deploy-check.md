Run a pre-deployment checklist before pushing changes to production on Render.

Check each item and report pass / fail / warning:

**Code**
- [ ] All Python files pass syntax check: `python -m py_compile main.py services/*.py`
- [ ] No `.env` file is tracked: `git ls-files .env` should return nothing
- [ ] No real credentials in `.env.example` (look for patterns like `AKIA[A-Z0-9]{16}` with actual values)
- [ ] `__pycache__/` and `*.pyc` are gitignored

**Templates**
- [ ] `dashboard.html` has no hardcoded amounts — all data comes from Jinja2 variables
- [ ] `upload.html` form action points to `/upload` and `enctype="multipart/form-data"` is set
- [ ] All three templates have the nav bar with correct links

**Render readiness**
- [ ] `render.yaml` has the correct `startCommand`: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- [ ] `requirements.txt` lists all imports used in the codebase

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
