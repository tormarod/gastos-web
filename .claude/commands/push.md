Push the current changes to GitHub with a well-structured commit message.

1. Run `git status` and `git diff` to understand exactly what changed.
2. Run `git log --oneline -5` to match the existing commit style.
3. Stage only the relevant files — never stage `.env`, `*.pyc`, or `__pycache__`.
4. Write a commit message that follows this format:
   - Subject line: imperative mood, under 60 chars, no period (e.g. "Fix delivery categorisation for Glovo")
   - Blank line
   - Body (if needed): what changed and why, not how
   - Blank line
   - `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
5. Commit, then run `git push origin main`.
6. Confirm the push succeeded and report the commit hash and message.

Never use `--no-verify`. Never amend a commit that has already been pushed.
If there is nothing to commit, say so clearly.
