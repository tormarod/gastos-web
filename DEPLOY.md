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
3. Clic en **"Create bucket"**

## Paso 2 — Crear un usuario IAM con acceso solo a S3

1. Ve a **IAM → Users → Create user**
   - Username: `gastos-web-app`
2. En **Permissions → Attach policies directly**, busca y selecciona `AmazonS3FullAccess`
   - (O crea una política personalizada solo para ese bucket si quieres más seguridad)
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

---

## Uso mensual

Cada mes, cuando tengas el extracto BBVA:
1. Abre la app en el navegador
2. Ve a **"Subir Extracto"**
3. Selecciona el mes correcto
4. Arrastra el fichero `.xlsx` descargado de BBVA
5. El dashboard se actualiza instantáneamente

## Actualizar la app

Cualquier cambio en el código → `git push` → Render despliega automáticamente.

---

## Notas sobre el plan gratuito de Render

- La app **duerme** tras 15 minutos sin peticiones
- Al acceder de nuevo tarda ~30 segundos en despertarse (normal)
- Si esto molesta, en el futuro puedes hacer ping automático con un cron externo (UptimeRobot, gratis)
