# Despliegue en Render + AWS S3 + Enable Banking

## Resumen

- **App**: Render.com (gratis, duerme tras 15 min sin uso, se despierta en ~30s)
- **Datos**: AWS S3 (gratis en free tier: 5 GB, 20K peticiones/mes)
- **Banco**: Enable Banking en modo restringido (gratis para vuestras propias cuentas, solo lectura, PSD2)
- **Sincronización diaria**: GitHub Actions (gratis) llama a la app cada mañana
- **Sin base de datos**: los datos financieros se guardan como JSON en S3

---

## Paso 1 — Crear el bucket S3 en AWS

1. Entra en [console.aws.amazon.com](https://console.aws.amazon.com) → **S3**
2. Clic en **"Create bucket"**
   - Name: `gastos-rodrigo-rocio` (o el nombre que quieras)
   - Region: `eu-west-1` (Irlanda, la más cercana)
   - Block all public access: **activado** (los datos son privados)
   - Bucket Versioning: **Enable**. Así cualquier cambio se puede deshacer recuperando una versión anterior de `ledger.json`.
3. Clic en **"Create bucket"**

Si el bucket ya existe: **Properties → Bucket Versioning → Edit → Enable**.

## Paso 2 — Crear un usuario IAM con acceso solo a ese bucket

1. Ve a **IAM → Users → Create user**
   - Username: `gastos-web-app`
2. En **Permissions → Attach policies directly → Create policy → JSON**, pega esto (cambia el nombre del bucket si es otro):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": "arn:aws:s3:::gastos-rodrigo-rocio/*"
       },
       {
         "Effect": "Allow",
         "Action": "s3:ListBucket",
         "Resource": "arn:aws:s3:::gastos-rodrigo-rocio"
       }
     ]
   }
   ```

   `ListBucket` hace falta para que S3 responda "no existe" (y no "acceso denegado") cuando la app busca un fichero que aún no se ha creado.
   Si ya usabais `AmazonS3FullAccess`, sustituidla por esta política: da acceso solo a vuestro bucket.
3. Ve a **Security credentials → Create access key**
   - Use case: "Application running outside AWS"
4. Guarda el **Access Key ID** y **Secret Access Key** — solo se muestran una vez

## Paso 3 — Subir el código a GitHub

```bash
cd C:\Users\torma\Projects\gastos-web
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/TU-USUARIO/gastos-web.git
git push -u origin main
```

## Paso 4 — Crear el servicio en Render

1. Ve a [render.com](https://render.com) → **New → Web Service**
2. Conecta tu repositorio de GitHub (`gastos-web`)
3. Render detectará el `render.yaml` automáticamente
4. En **Environment Variables**, añade manualmente:
   | Variable | Valor |
   |---|---|
   | `APP_PASSWORD` | La contraseña que queráis compartir |
   | `AWS_ACCESS_KEY_ID` | Del paso 2 |
   | `AWS_SECRET_ACCESS_KEY` | Del paso 2 |
   | `S3_BUCKET_NAME` | `gastos-rodrigo-rocio` |
   | `PUBLIC_URL` | La URL de la app, p. ej. `https://gastos-web.onrender.com` |
5. Clic en **"Create Web Service"**

Render desplegará la app automáticamente. La URL será algo como `https://gastos-web.onrender.com`.

En producción (`ENV=production`) la app **no arranca** si faltan `SECRET_KEY` o `APP_PASSWORD`, para no quedarse nunca con una contraseña por defecto. Si un despliegue falla por eso, Render mantiene la versión anterior en marcha.

La primera vez que se abre la nueva versión, la app convierte vuestros `data.json` y `merchant_rules.json` al formato nuevo (`ledger.json` y `rules.json`). Los ficheros antiguos no se borran. En **Revisar** aparece un resumen de la migración.

---

## Paso 5 — Conectar BBVA con Enable Banking

Enable Banking es un agregador PSD2 con un modo gratuito ("restricted production") para acceder a vuestras propias cuentas. La conexión es de solo lectura.

1. **Cuenta**: regístrate en [enablebanking.com](https://enablebanking.com) y entra en el **Control Panel**.
2. **Aplicación**: en **Applications → Add a new application**:
   - Environment: **Production**
   - Name: `Gastos Rodrigo & Rocío`
   - Allowed redirect URLs: `https://gastos-web.onrender.com/banco/callback` (vuestra `PUBLIC_URL` + `/banco/callback`)
   - Deja la opción de generar la clave en el navegador. Al guardar se descarga un fichero `.pem` con la **clave privada**: guárdalo bien, no se puede volver a descargar.
   - Anota el **Application ID**.
3. **Vincular la cuenta** (modo restringido): en la aplicación, pulsa **Activate by linking accounts**, elige **BBVA (España)**, entra con vuestro usuario de BBVA, confirma en la app del banco y selecciona **solo la cuenta común**. La aplicación quedará como "Restricted" y "Active".
4. **Render**: añade estas variables de entorno:
   | Variable | Valor |
   |---|---|
   | `ENABLE_BANKING_APP_ID` | El Application ID |
   | `ENABLE_BANKING_PRIVATE_KEY` | El contenido completo del `.pem` (Render acepta varias líneas; también vale en una línea con `\n`) |
   | `PUBLIC_URL` | La URL de la app (si no la pusiste en el paso 4) |

   Alternativa a pegar la clave: súbela como **Secret File** en Render (se monta en `/etc/secrets/<nombre>`) y define `ENABLE_BANKING_PRIVATE_KEY_PATH=/etc/secrets/<nombre>`.
5. **Conectar desde la app**: abre la app → **Banco → Conectar BBVA**. Vais a la web de BBVA, autorizáis, y al volver la app importa los últimos ~90 días. Si el banco devuelve varias cuentas, la app os pedirá cuál es la común.

El permiso dura hasta 180 días (depende del banco). Unas dos semanas antes aparece un aviso en la app; renovarlo es repetir el paso 5 (**Banco → Renovar permiso**).

## Paso 6 — Sincronización diaria (GitHub Actions)

El workflow `.github/workflows/sync.yml` llama a la app cada día a las 05:00 UTC (07:00 en verano, 06:00 en invierno en Madrid). Solo hace falta darle dos secretos:

1. En Render, copia el valor generado de `SYNC_TOKEN` (**Environment**).
2. En GitHub: **Settings → Secrets and variables → Actions → New repository secret**:
   | Secreto | Valor |
   |---|---|
   | `APP_URL` | `https://gastos-web.onrender.com` |
   | `SYNC_TOKEN` | El mismo valor que en Render |
3. Pruébalo: **Actions → Sincronizar banco → Run workflow**.

La respuesta solo contiene recuentos (nuevos, duplicados, por revisar), nunca datos de movimientos, así que los logs son seguros aunque el repositorio fuera público. Si el permiso del banco caduca o la sincronización falla, el workflow termina en rojo y GitHub os avisa por email.

Notas:
- En repositorios públicos, GitHub desactiva los workflows programados tras 60 días sin actividad en el repo. Si pasa, se reactivan desde la pestaña **Actions**.
- Alternativa sin GitHub: cualquier servicio de cron gratuito (p. ej. cron-job.org) que haga `POST https://…/api/sync` con la cabecera `Authorization: Bearer <SYNC_TOKEN>`.
- Además, si abrís la app y la última sincronización tiene más de 20 horas, se lanza una en segundo plano.

---

## Uso diario

- Los movimientos llegan solos cada mañana (los que el banco ya ha contabilizado, normalmente 1–3 días después del pago).
- Lo que la app no sabe categorizar aparece en **Revisar**. Cada regla que guardáis se aplica a todos los movimientos, pasados y futuros.
- **Subir extracto** sigue funcionando para cargar meses antiguos o si el banco falla. No hace falta elegir el mes y los solapes no duplican nada.

## Actualizar la app

Cualquier cambio en el código → `git push` → Render despliega automáticamente.

Antes de subir cambios: `pip install -r requirements-dev.txt && pytest`.

---

## Notas sobre el plan gratuito de Render

- La app **duerme** tras 15 minutos sin peticiones
- Al acceder de nuevo tarda ~30 segundos en despertarse (normal)
- El cron diario también la despierta; si la espera molesta, el plan Starter de Render (unos 7 $/mes) no duerme
