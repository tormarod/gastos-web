Review and improve the transaction categorisation rules in `services/categorizer.py`.

1. Read `services/categorizer.py` to see the current RULES list.
2. If the user provided a concept string or merchant name, find which rule it matches (or doesn't) and explain why.
3. If the user wants to add a new merchant or fix a miscategorisation:
   - Add the keyword to the correct category tuple in RULES, or create a new category tuple if needed.
   - Keep keywords UPPERCASE to match the normalisation in `categorize()`.
   - Add a trailing space after short words to avoid partial matches (e.g. "BAR " not "BAR").
   - Place more specific rules before more general ones.
4. After editing, show a summary of what changed and give 2-3 example BBVA concept strings that would now match the new rule.
5. Use `/push` to commit and deploy the change.
