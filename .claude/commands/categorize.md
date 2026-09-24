Review and improve the transaction categorisation rules in `services/categorizer.py`.

1. Read `services/categorizer.py` to see the current RULES list and the order in `classify()`.
2. If the user provided a concept string or merchant name, run `classify(concept, amount)` for it and explain which layer decided (user rule, income keyword, built-in keyword, MCC or fallback) and why.
3. If the user wants to add a new merchant or fix a miscategorisation:
   - Prefer telling them to save a rule from **Revisar** in the app when it is specific to them; add a built-in keyword when it is a common Spanish merchant.
   - Keywords are UPPERCASE without accents and match whole words: no trailing-space tricks needed.
   - Use `*` at the end for prefixes (`PSICOLOG*`), a leading non-alphanumeric character is literal (`*EATS`), and `~` marks a weak generic word (`~TIENDA`) that only counts when nothing else matches.
   - The longest matching keyword wins across categories, so a more specific keyword (ZARA HOME) can live in another category than the short one (ZARA).
   - Remember the sign: credits only look at income keywords first; a credit matching a spending keyword is a refund.
4. Add a case to `tests/test_categorizer.py` and run `pytest tests/test_categorizer.py`.
5. After editing, show a summary of what changed and give 2-3 example BBVA concept strings that would now match the new rule.
6. Use `/push` to commit and deploy the change.
