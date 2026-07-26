# ACV — backend del formulario de captación

Servicio en Python que corre por cron (sin servidor persistente) y en cada
corrida:

1. Consulta la base de Notion **"Solicitudes ACV"** buscando filas nuevas o
   sin procesar.
2. Para cada fila: si el `Tipo` es **Clases**, crea un evento con hora en
   Google Calendar; si es **Asesoría**, crea un evento de todo el día en la
   fecha límite.
3. Envía un correo de confirmación por Gmail (con un `.ics` adjunto).
4. Marca la fila como `confirmado` en Notion.

El formulario en sí vive en NoteForms (plan gratis, sin webhooks) y ya
escribe directamente en la base de Notion — este repo no recibe nada de
NoteForms, solo lee y actualiza Notion por polling.

## Por qué polling y no webhook

El plan gratis de NoteForms no incluye webhooks salientes. En cambio, el
formulario ya está conectado nativamente a Notion (esa integración la maneja
NoteForms). El backend entonces consulta Notion cada pocos minutos por cron
en vez de esperar una notificación push.

## Estructura

```
scripts/poll_notion.py       # entry point: corre un ciclo completo de polling
lib/config.py                # carga y valida variables de entorno
lib/schemas.py                # parsea una fila (página) de Notion a un objeto tipado
lib/notion_client.py           # consulta filas pendientes + marca como procesadas
lib/calendar_client.py          # crea eventos en Google Calendar
lib/ics.py                       # genera el adjunto .ics
lib/email_client.py               # envía el correo por Gmail SMTP (texto fácil de editar)
.github/workflows/poll.yml        # cron cada 15 minutos
tests/                              # pytest, mockea todas las APIs externas
```

## Decisiones clave

- **Scheduling: GitHub Actions**, no Vercel Cron ni cron-job.org. El workflow
  ejecuta `scripts/poll_notion.py` directo — no hace falta desplegar nada
  como servicio web. Vercel Cron en el plan Hobby (gratis) limita los cron
  jobs a una corrida por día, insuficiente para "cada pocos minutos" sin
  pasar a Pro (US$20/mes). Con el repo **privado**, un intervalo de 15
  minutos entra holgado en los 2000 minutos/mes gratis de Actions; si en
  algún momento el repo pasa a público, los minutos son ilimitados y se
  puede bajar a 5 minutos. Riesgo conocido: GitHub desactiva los workflows
  programados tras 60 días sin commits en el repo, y en horas pico puede
  correrlos con algo de retraso — aceptable para este caso de uso.
- **Asesoría sí crea evento** (de todo el día, en la fecha límite), además de
  guardar en Notion y mandar el correo — así también queda una alerta visual
  en tu calendario, no solo en Notion.
- **Google Calendar vía service account**, no OAuth: automatización
  desatendida sin mantener vivo un refresh token. Compartiste tu calendario
  personal con el email de la cuenta de servicio con permiso de "hacer
  cambios en los eventos" — confirmado. Como contrapartida, **no se agregan
  invitados (`attendees`) a los eventos**: las service accounts sin
  Domain-Wide Delegation no pueden invitar asistentes en calendarios ajenos;
  la confirmación al cliente/alumno va solo por email con el `.ics` adjunto.
- **Idempotencia sin columna de submission id**: como ahora se lee Notion
  directamente (no un payload externo), el `page_id` que ya trae cada fila
  alcanza como clave única. La columna `Submission ID` que había quedado del
  diseño anterior no se usa (podés borrarla si querés, o dejarla vacía sin
  problema). Cada paso se trackea por separado (`Calendar Event ID`, `Email
  Enviado`), así que si una corrida falla a la mitad, la siguiente corrida
  retoma solo lo que faltó, sin duplicar nada.
- **Nombres de propiedades**: confirmados contra la base real vía la API
  (ver sección siguiente). Notá que la columna de fecha se llama `"Fecha "`
  **con un espacio al final** — así está hoy en Notion. Si la renombrás para
  sacarle el espacio, actualizá `PROP_FECHA` en `lib/schemas.py`.
- **Filas incompletas no rompen la corrida**: si una fila no tiene todavía
  `Nombre`/`Correo`/`Tipo`/`Fecha` (por ejemplo, mientras NoteForms la está
  escribiendo, o una fila de prueba creada a mano), se loggea como fila
  inválida y se sigue con las demás. Esa fila va a reaparecer en cada corrida
  hasta que se completen los campos o se borre — es un comportamiento
  esperado, no un bug.
- **Gmail en vez de Resend**: no hay un dominio propio verificado, y Resend
  en modo prueba solo deja mandar correo a la propia dirección registrada —
  no sirve para mandarle confirmaciones a clientes reales. Gmail (vía SMTP +
  App Password) no exige dominio para mandar a cualquier destinatario, es
  gratis, y ya tenés la cuenta. Contras conocidos: el remitente se ve como un
  Gmail personal en vez de una dirección de marca, y el límite de envío de
  Gmail es de ~500 correos/día en una cuenta personal (muy por encima del
  volumen esperado acá). Si más adelante conseguís un dominio propio, se
  puede volver a un proveedor transaccional sin tocar el resto del código —
  el único punto de cambio es `lib/email_client.py`.

## Encontrar el data source id de Notion

Este proyecto usa la versión actual de la API de Notion, donde las
propiedades y filas de una base viven en un **data source** (no directamente
en el "database" contenedor). Para encontrar el id, con `NOTION_TOKEN` ya
configurado en el entorno:

```bash
python3 - <<'EOF'
import os
from notion_client import Client

client = Client(auth=os.environ["NOTION_TOKEN"])
results = client.search(
    query="Solicitudes ACV",
    filter={"property": "object", "value": "data_source"},
)["results"]
for r in results:
    title = "".join(t["plain_text"] for t in r["title"])
    print(title, "->", r["id"])
EOF
```

Ese id es `NOTION_DATA_SOURCE_ID`.

## Modelo de datos en Notion

Propiedades reales de la base "Solicitudes ACV" (confirmadas vía API):

| Propiedad           | Tipo         | Notas                                              |
|----------------------|--------------|-----------------------------------------------------|
| Nombre               | title        |                                                       |
| Tipo                  | select       | opciones: `Asesoría`, `Clases`                       |
| Correo                | email        |                                                       |
| `Fecha ` (con espacio)| date         | fecha límite (Asesoría) o fecha/hora (Clases)        |
| Estado                | select       | opciones: `nuevo`, `confirmado`                      |
| Calendar Event ID     | rich text    | lo escribe el backend, no tocar a mano               |
| Email Enviado         | checkbox     | lo escribe el backend                                |
| Submission ID         | rich text    | sin uso actualmente (ver "Decisiones clave")         |
| Creado                | created time |                                                       |

**Importante**: para que el polling funcione, tenés que reconectar el
formulario en NoteForms para que escriba en **esta** base ("Solicitudes
ACV") — hoy está escribiendo en otra base autogenerada ("Solicitud de
asesorías y clases") que no tiene las columnas `Estado`/`Calendar Event
ID`/`Email Enviado`. En NoteForms: configuración del formulario →
integración con Notion → cambiar la base de destino a "Solicitudes ACV" y
mapear los campos (`Nombre completo` → `Nombre`, `Correo electrónico` →
`Correo`, `Tipo de solicitud` → `Tipo`, `Fecha` → `Fecha `).

## Configurar credenciales

### Notion

1. La integración interna "ACV Formulario Backend" ya existe y ya está
   compartida con la base "Solicitudes ACV" — usá su token como
   `NOTION_TOKEN`.
2. Sacá `NOTION_DATA_SOURCE_ID` con el script de la sección anterior.

### Google Calendar (service account)

Ya creaste la cuenta de servicio (`calendar-writer@...iam.gserviceaccount.com`)
y compartiste tu calendario con permiso de "hacer cambios en los eventos".
Falta:

1. En Google Cloud Console → la cuenta de servicio → `Claves` → `Agregar
   clave` → JSON (si todavía no descargaste el archivo).
2. Minificá ese JSON a una sola línea (o codificalo en base64) y pegalo como
   `GOOGLE_SERVICE_ACCOUNT_JSON`. `lib/config.py` acepta ambos formatos.
3. `GOOGLE_CALENDAR_ID` es tu email (`andrescubillo07@gmail.com`) si es tu
   calendario principal.

### Gmail (envío de correo)

1. En la cuenta de Gmail que va a mandar los correos (`andrescubillo07@gmail.com`),
   activá la verificación en 2 pasos si todavía no la tenés:
   [myaccount.google.com/security](https://myaccount.google.com/security) →
   `Verificación en 2 pasos`.
2. Con eso activado, andá a
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   → creá una App Password nueva (cualquier nombre, ej. "ACV backend") →
   Google te muestra un código de 16 caracteres una sola vez.
3. Ese código (sin espacios o con ellos, da igual) va en
   `GMAIL_APP_PASSWORD`. `GMAIL_ADDRESS` es la misma dirección de Gmail.
4. No hay modo prueba ni restricción de destinatario: podés mandar a
   cualquier correo desde el primer envío.

El texto de los correos está en `lib/email_client.py`, en las funciones
`_asesoria_email` y `_clases_email` — son las únicas dos funciones a editar
si querés cambiar el copy.

## Probar en local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# completar .env con credenciales reales

pytest              # corre toda la suite (mockea las APIs externas)

python scripts/poll_notion.py   # corre un ciclo real contra Notion/Calendar/Gmail
```

## Programar la corrida (GitHub Actions)

1. En el repo de GitHub: `Settings` → `Secrets and variables` → `Actions` →
   agregar como secrets: `NOTION_TOKEN`, `NOTION_DATA_SOURCE_ID`,
   `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `GOOGLE_SERVICE_ACCOUNT_JSON`,
   `GOOGLE_CALENDAR_ID`.
2. El workflow `.github/workflows/poll.yml` ya está configurado para correr
   cada 15 minutos (`workflow_dispatch` también permite correrlo a mano desde
   la pestaña Actions para probar).
3. Hacé push del repo a GitHub — el cron arranca solo desde ahí, no hace
   falta ningún paso de "deploy" adicional.

## Desplegar

No hay que desplegar nada: GitHub Actions **es** la infraestructura de
ejecución. Solo hace falta pushear el repo con el workflow y los secrets
configurados (paso anterior).
