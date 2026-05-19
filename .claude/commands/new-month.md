Guide through uploading a new month's bank statement and verifying the dashboard updated correctly.

Walk the user through these steps:

1. **Export from BBVA**
   - Log in to bbva.es → Cuentas → select the shared account
   - Go to the month's statement → Descargar → select **Excel (.xlsx)**
   - Save the file somewhere easy to find (e.g. Downloads)

2. **Upload to the app**
   - Open the app URL in the browser
   - Go to **Subir Extracto**
   - Select the correct month (YYYY-MM format)
   - Drag the downloaded `.xlsx` file into the upload zone
   - Click **Procesar y guardar**

3. **Verify**
   - Confirm the success message appears with the correct month label
   - Go back to the Dashboard and check:
     - The new month appears in the monthly chart
     - The KPIs (income, expenses, balance) look reasonable
     - The top transactions in the table are from the new month
   - If any transactions have category "Otros" and you think they should be categorised, use `/categorize` to add the rule.

4. **If the upload fails**
   - Check the error message — most likely causes:
     - Wrong file format (must be `.xlsx`, not `.xls` or `.pdf`)
     - BBVA changed their export layout → report to Claude so the parser can be updated
   - You can re-upload the same month; it will overwrite the previous data for that month.
