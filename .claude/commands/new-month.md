Help the user get a month's movements into the app and check the dashboard updated correctly.

1. **Is the bank connected?**
   - Open the app → **Banco**. If it says *Conectado* and the last sync is recent, movements arrive by themselves every morning: skip to step 3.
   - If it says the consent expired or is about to: **Renovar permiso** and authorise in BBVA.

2. **Otherwise, upload the Excel**
   - Log in to bbva.es → Cuentas → the shared account → download the movements as **Excel (.xlsx)**. Several months at once is fine.
   - Open the app → **Subir extracto** → drop the file(s) → **Importar**. No month to choose; movements already stored are skipped.

3. **Verify**
   - The upload message (or Banco) shows how many movements were new and how many were already there.
   - Dashboard: the month appears in the chart and the KPIs look reasonable.
   - **Revisar**: assign a category to anything pending. Saving a rule applies it to every past and future movement. For a merchant that should be a built-in keyword, use `/categorize`.

4. **If something fails**
   - Wrong format (must be `.xlsx`, not `.xls` or `.pdf`), or BBVA changed their export layout → report to Claude so the parser can be updated.
   - Bank errors: the detail is on the **Banco** page; the daily GitHub Action also turns red when the consent expires.
