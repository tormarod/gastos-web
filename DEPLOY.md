# Despliegue en Render + AWS S3

## Resumen

- **App**: Render.com (gratis, duerme tras 15 min sin uso, se despierta en ~30s)
- **Datos**: AWS S3 (gratis en free tier: 5 GB, 20K peticiones/mes)
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
5. Clic en **"Create Web Service"**

Render desplegará la app automáticamente. La URL será algo como `https://gastos-web.onrender.com`.

En producción (`ENV=production`) la app **no arranca** si faltan `SECRET_KEY` o `APP_PASSWORD`, para no quedarse nunca con una contraseña por defecto. Si un despliegue falla por eso, Render mantiene la versión anterior en marcha.

La primera vez que se abre la nueva versión, la app convierte vuestros `data.json` y `merchant_rules.json` al formato nuevo (`ledger.json` y `rules.json`). Los ficheros antiguos no se borran. En **Revisar** aparece un resumen de la migración.

---

## Uso mensual

Cada mes, o cuando queráis:
1. Entrad en BBVA online y descargad los movimientos de la cuenta común en **Excel (.xlsx)**. Pueden ser varios meses de golpe.
2. Abrid la app → **Subir extracto** → arrastrad el fichero. No hace falta elegir el mes y los solapes no duplican nada.
3. Lo que la app no sabe categorizar aparece en **Revisar**. Cada regla que guardáis se aplica a todos los movimientos, pasados y futuros.

La conexión automática con el banco (Enable Banking) queda para una versión posterior; está preparada en un PR aparte.

## Actualizar la app

Cualquier cambio en el código → `git push` → Render despliega automáticamente.

Antes de subir cambios: `pip install -r requirements-dev.txt && pytest`.

---

## Notas sobre el plan gratuito de Render

- La app **duerme** tras 15 minutos sin peticiones
- Al acceder de nuevo tarda ~30 segundos en despertarse (normal)
- Si la espera molesta, el plan Starter de Render (unos 7 $/mes) no duerme
