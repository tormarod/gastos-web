Help the user get a month's movements into the app and check Inicio and Análisis updated correctly.

1. **Export from BBVA**
   - Log in to bbva.es → Cuentas → the shared account → download the movements as **Excel (.xlsx)**. Several months at once is fine.

2. **Upload to the app**
   - Open the app → **Añadir** → **Extracto del banco** → drop the file(s) → **Importar**. No month to choose; movements already stored are skipped.

3. **Verify**
   - The upload message shows how many movements were new and how many were already there.
   - Inicio: "Datos hasta el …" shows the last movement and the stale-data banner is gone.
   - Movimientos: the new movements are there, grouped by day.
   - Análisis: the month appears in the charts and the averages look reasonable.
   - **Revisar**: assign a category to anything pending. Saving a rule applies it to every past and future movement. For a merchant that should be a built-in keyword, use `/categorize`.

4. **If the upload fails**
   - Wrong format (must be `.xlsx`, not `.xls` or `.pdf`), or BBVA changed their export layout → report to Claude so the parser can be updated.
