# LMS Administración y Gestión

LMS modular para la familia profesional de **Administración y Gestión**, orientado a Formación Profesional y preparado para trabajar con **SCORM, evaluación por RA/CE, varios profesores y grupos, IA configurable por docente e integración LTI 1.3 Advantage con CAMPUS/Moodle**.

Versión actual: **1.0.0-rc2**

> El software bajo control del repositorio está cubierto por CI funcional y por una prueba del stack Docker completo. La validación contra CAMPUS real y el alta institucional LTI siguen dependiendo de infraestructura externa.

---

## 1. Inicio rápido

### Instalación recomendada en un Ubuntu nuevo

Si el servidor ya tiene `curl`:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  -o /tmp/lms-bootstrap.sh

sudo bash /tmp/lms-bootstrap.sh \
  --url https://lms.midominio.es
```

El sistema se instala por defecto en:

```text
/opt/lms-administracion-y-gestion
```

El bootstrap instala las dependencias necesarias, clona el repositorio, prepara Docker, crea secretos, levanta PostgreSQL + FastAPI + Nginx, aplica migraciones, aprovisiona el catálogo y ejecuta la verificación final.

### Verificación

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/verify-installation.sh
```

El resultado correcto termina con:

```text
OK: instalación interna operativa.
```

También deben responder:

```text
https://lms.midominio.es/
https://lms.midominio.es/api/health
https://lms.midominio.es/lti/jwks
```

---

## 2. Servidor Ubuntu totalmente limpio

Si el servidor está recién instalado y no tiene ni `git` ni `curl`, prepara primero Ubuntu:

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  git
```

Comprueba las herramientas:

```bash
git --version
curl --version
```

Si Ubuntu solicita reinicio:

```bash
if [ -f /var/run/reboot-required ]; then
  sudo reboot
fi
```

Después del reinicio, vuelve a conectarte por SSH y ejecuta el bootstrap del apartado anterior.

> No es necesario instalar manualmente Docker, Docker Compose, PostgreSQL, Python ni Nginx. El instalador del proyecto prepara esos componentes.

---

## 3. Instalación en producción

### 3.1. Requisitos

- Ubuntu con acceso `sudo` o root.
- Salida a Internet para APT, GitHub y Docker Hub.
- Una URL pública HTTPS, por ejemplo `https://lms.midominio.es`.
- Opcionalmente, un token de Cloudflare Tunnel.

### 3.2. Instalación desatendida estándar

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  -o /tmp/lms-bootstrap.sh

sudo bash /tmp/lms-bootstrap.sh \
  --url https://lms.midominio.es
```

Si el proxy o túnel HTTPS se administra externamente, debe enviar tráfico hacia:

```text
http://127.0.0.1:8080
```

### 3.3. Instalación con Cloudflare Tunnel

Para no dejar el token en el historial del shell:

```bash
sudo install -m 600 /dev/null /root/cloudflare-lms.token
sudo nano /root/cloudflare-lms.token
```

Pega únicamente el token del túnel y ejecuta:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  -o /tmp/lms-bootstrap.sh

sudo bash /tmp/lms-bootstrap.sh \
  --url https://lms.midominio.es \
  --cloudflare-token-file /root/cloudflare-lms.token
```

Cuando se utiliza el contenedor `cloudflared`, el hostname público del túnel debe apuntar internamente a:

```text
http://web:8080
```

No a `localhost:8080` dentro del contenedor.

### 3.4. Instalación mediante fichero de configuración

El repositorio incluye:

```text
deploy/unattended.env.example
```

Ejemplo de `/root/lms-install.env`:

```bash
LMS_PUBLIC_BASE_URL=https://lms.midominio.es
CLOUDFLARE_TUNNEL_TOKEN=TOKEN_DEL_TUNEL
LMS_INSTALL_DIR=/opt/lms-administracion-y-gestion
LMS_INSTALL_REPOSITORY=https://github.com/atreyu1968/LMS-Adminidtracion-y-Gestion.git
LMS_INSTALL_BRANCH=main
LMS_INSTALL_PROVISION=1
```

Protégelo:

```bash
sudo chmod 600 /root/lms-install.env
```

Y ejecuta:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  -o /tmp/lms-bootstrap.sh

sudo bash /tmp/lms-bootstrap.sh \
  --config /root/lms-install.env
```

Este formato es apropiado para cloud-init, Ansible u otros sistemas de aprovisionamiento.

### 3.5. Instalación manual desde Git

```bash
sudo mkdir -p /opt
cd /opt

sudo git clone \
  https://github.com/atreyu1968/LMS-Adminidtracion-y-Gestion.git \
  lms-administracion-y-gestion

cd /opt/lms-administracion-y-gestion

sudo bash scripts/install-ubuntu.sh \
  --url https://lms.midominio.es
```

### 3.6. Instalar solo el motor LMS

Para omitir el aprovisionamiento inicial de GTH y NOMINASOL:

```bash
sudo bash /tmp/lms-bootstrap.sh \
  --url https://lms.midominio.es \
  --no-provision
```

---

## 4. Qué hace automáticamente el instalador

El proceso de instalación:

1. instala `ca-certificates`, `curl`, `git`, Docker y Docker Compose;
2. clona el LMS en el directorio configurado;
3. genera contraseña PostgreSQL, secreto de sesión, token administrativo y secreto de cifrado de IA;
4. crea `.env` con permisos restringidos;
5. genera la clave RSA LTI dentro del almacenamiento persistente;
6. levanta PostgreSQL, FastAPI y Nginx;
7. levanta `cloudflared` si se proporciona un token;
8. aplica automáticamente las migraciones de base de datos;
9. aprovisiona GTH y NOMINASOL salvo que se utilice `--no-provision`;
10. comprueba API, versión, JWKS, esquema, almacenamiento, Nginx y autodiagnóstico;
11. genera un informe de instalación en:

```text
/var/log/lms-administracion-y-gestion-install.txt
```

El informe no incluye contraseñas ni tokens.

---

## 5. Operación del servidor

### 5.1. Actualizar Ubuntu

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
```

Si se requiere reinicio:

```bash
if [ -f /var/run/reboot-required ]; then
  sudo reboot
fi
```

### 5.2. Actualizar el LMS

Después de volver a conectarte:

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/update-ubuntu.sh
```

El actualizador realiza automáticamente:

1. backup previo;
2. `git fetch` y `git pull --ff-only`;
3. actualización/reconstrucción de contenedores;
4. migraciones de base de datos;
5. espera hasta que la API esté disponible;
6. `verify-installation.sh`;
7. conservación del backup previo para poder restaurar.

Después:

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/verify-installation.sh
docker compose ps
```

### 5.3. Reinstalar Git o curl

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates git curl
```

Esto no modifica los datos del LMS.

### 5.4. Backup

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/backup.sh
```

La copia incluye:

- PostgreSQL;
- almacenamiento persistente;
- secretos necesarios;
- versión;
- hashes SHA-256.

### 5.5. Restauración

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/restore.sh backups/FECHA --yes
```

La restauración verifica integridad, recupera base y almacenamiento, reconstruye servicios, aplica las migraciones actuales y vuelve a comprobar el sistema.

### 5.6. Diagnóstico

```bash
cd /opt/lms-administracion-y-gestion

set -a
source .env
set +a

curl -fsS \
  -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/admin/readiness
```

`code_ready: true` confirma que el software interno está preparado.

`campus_ready` permanecerá en `false` hasta que exista una URL pública válida y se haya registrado una plataforma LTI real.

### 5.7. Ficheros importantes

```text
/opt/lms-administracion-y-gestion/.env
    configuración y secretos; permisos 600

/var/log/lms-administracion-y-gestion-install.txt
    informe de instalación

/opt/lms-administracion-y-gestion/backups/
    copias de seguridad

deploy/unattended.env.example
    plantilla de despliegue automatizado
```

Para consultar únicamente el token administrativo:

```bash
sudo grep '^LMS_ADMIN_TOKEN=' \
  /opt/lms-administracion-y-gestion/.env
```

No publiques ni copies el contenido completo de `.env`.

---

## 6. Acceso al LMS

El LMS admite **dos vías de autenticación paralelas**:

- **CAMPUS / LTI 1.3**: acceso institucional desde Moodle.
- **Acceso directo local**: usuario y contraseña propios del LMS en `/login.html`.

La identidad es la misma en ambos casos. Un profesor que ya exista por CAMPUS puede entrar una vez mediante LTI, abrir:

```text
/account.html
```

o utilizar **Mi acceso directo** desde el panel docente, y crear sus credenciales locales. A partir de ese momento puede entrar directamente en:

```text
https://TU_DOMINIO/login.html
```

Las credenciales locales:

- no sustituyen ni modifican la identidad LTI;
- conservan los mismos grupos, módulos, permisos y progreso;
- almacenan únicamente un hash PBKDF2-SHA256 con salt aleatorio;
- bloquean temporalmente el acceso después de varios intentos fallidos;
- permiten seleccionar el grupo activo cuando el usuario pertenece a más de uno.

La portada `/` permite cambiar de grupo y ofrece acceso tanto al panel docente como a la configuración de credenciales.

---

## 7. Uso docente

El panel principal del profesor se sirve en:

```text
/teacher.html
```

Desde él se gestionan:

- grupos locales y grupos procedentes de CAMPUS;
- profesorado colaborador;
- alumnado e importación CSV;
- catálogo de módulos;
- módulos propios;
- biblioteca SCORM privada y compartida;
- biblioteca multimedia;
- configuración personal de IA;
- evaluación, progreso y disponibilidad.

### 6.1. Biblioteca y edición SCORM

Cada profesor puede:

- importar SCORM 1.2 o SCORM 2004;
- reutilizar el mismo paquete en varios módulos;
- compartir paquetes;
- crear copias editables de paquetes compartidos;
- editar contenido, HTML, CSS, JavaScript y recursos;
- incorporar vídeo, audio, imágenes y PDF;
- publicar revisiones nuevas;
- exportar un SCORM individual, un borrador o la biblioteca completa.

Los intentos ya iniciados quedan fijados a la revisión con la que comenzaron. Publicar una revisión nueva no altera notas ni registros anteriores.

Documentación: `docs/SCORM_EDITOR.md`.

### 6.2. Espacio docente

La arquitectura completa de grupos, permisos, módulos y bibliotecas se documenta en:

```text
docs/TEACHER_WORKSPACE.md
```

---

## 8. Contenidos oficiales

### 7.1. Gestión de Recursos Humanos — GTH / 0652

La migración pública incluye:

- 4 RA;
- 33 CE;
- 198 actividades públicas;
- 4 SCORM finales;
- evaluación;
- examen;
- recuperación;
- IA por profesor;
- AGS por RA.

Los bancos privados reales permanecen fuera de Git y deben cargarse desde almacenamiento seguro.

Documentación:

```text
docs/GTH_MIGRATION.md
```

Los cuatro SCORM finales están disponibles en:

```text
downloads/gth0652/
```

### 7.2. Proyecto guiado NOMINASOL 2026

Se incluye un proyecto profesional guiado para **TeamSystem NOMINASOL 2026 · Versión Educativa**.

Flujo de aprendizaje:

```text
situación profesional
→ explicación didáctica
→ operación en NOMINASOL
→ autocontrol
→ evidencia
→ comprobación IA / docente
→ corrección si procede
→ hito superado
→ siguiente fase
```

La IA utiliza exclusivamente la API configurada por cada profesor. Las evidencias conservan su historial y los hitos críticos o de baja confianza pasan a revisión humana.

Documentación:

```text
docs/NOMINASOL_GUIDED_PROJECT.md
```

Aprovisionamiento manual:

```bash
curl -X POST \
  -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  "$LMS_PUBLIC_BASE_URL/api/admin/catalog/nominasol2026/provision"
```

---

## 9. Integración con CAMPUS / Moodle

El LMS implementa **LTI 1.3 Advantage**:

- **LTI Core 1.3 + OIDC**: acceso sin una segunda contraseña.
- **Deep Linking**: selección de módulo o RA desde CAMPUS.
- **NRPS**: sincronización de alumnado, profesorado y roles.
- **AGS**: devolución de calificaciones definitivas al calificador.

Endpoints principales:

```text
/lti/login
/lti/launch
/lti/jwks
/lti/deep-link
```

El LMS puede insertar en CAMPUS:

- un módulo completo;
- un RA concreto.

Las calificaciones provisionales no se envían mediante AGS. Solo se devuelve una nota cuando el resultado está en estado definitivo.

La activación real requiere que la administración de CAMPUS registre la herramienta y conceda los servicios LTI correspondientes.

Documentación: `docs/CAMPUS_LTI.md`.

---

## 10. Arquitectura

```text
CAMPUS / Moodle
    │
    │ LTI 1.3 Advantage
    ▼
┌────────────────────────────────────────────┐
│ LMS Administración y Gestión              │
│                                            │
│ FastAPI                                    │
│ ├─ identidad LTI / sesiones               │
│ ├─ centros, cursos, profesores, alumnos   │
│ ├─ catálogo de módulos                    │
│ ├─ motor SCORM 1.2 / 2004                 │
│ ├─ evaluación RA / CE                     │
│ ├─ evidencias / proyectos guiados         │
│ ├─ IA configurable por profesor           │
│ ├─ AGS → calificaciones a CAMPUS          │
│ └─ NRPS → matrículas y roles              │
│                                            │
│ PostgreSQL        Almacenamiento SCORM     │
└────────────────────────────────────────────┘
```

### Principios de diseño

1. **Multi-módulo**: el núcleo no está acoplado a GTH.
2. **Multi-profesor**: cada grupo puede tener varios docentes.
3. **Multi-centro**: preparado para varias organizaciones.
4. **CAMPUS-first**: la identidad institucional procede de LTI.
5. **SCORM genérico**: importación, reproducción, edición y versionado.
6. **Evaluación trazable**: intentos, evidencias, revisiones y notas auditables.
7. **IA por profesor**: cada docente aporta su propia API si quiere utilizarla.
8. **Sin secretos en Git**: claves, bancos privados y datos personales permanecen fuera del repositorio.
9. **Revisiones no destructivas**: editar contenido no invalida intentos ya iniciados.

---

## 11. Estado funcional

### Implementado

- [x] PostgreSQL + FastAPI + Docker.
- [x] Diseño multi-módulo y multi-profesor.
- [x] Grupos locales y cursos CAMPUS unificados.
- [x] Profesores colaboradores.
- [x] Importación de alumnado por CSV.
- [x] Panel docente.
- [x] Catálogo de módulos oficiales.
- [x] Biblioteca SCORM privada/compartida.
- [x] SCORM 1.2 y SCORM 2004.
- [x] Editor SCORM versionado.
- [x] Exportación individual, borradores y biblioteca.
- [x] Biblioteca multimedia con HTTP Range.
- [x] Centro de actividad docente.
- [x] Gradebook RA/CE.
- [x] Progreso y detalle de alumno.
- [x] Reglas de disponibilidad, secuenciación, excepciones y exenciones.
- [x] IA personal por profesor.
- [x] Proyecto guiado y evidencias.
- [x] GTH público migrado.
- [x] NOMINASOL 2026.
- [x] LTI 1.3 Core / OIDC.
- [x] Deep Linking.
- [x] NRPS.
- [x] AGS.
- [x] Instalación Ubuntu desatendida.
- [x] Migraciones de base de datos.
- [x] Backup y restore verificados.
- [x] CI funcional + stack Docker completo.

### Pendiente de sistemas externos

- [ ] cargar los bancos privados reales de GTH desde almacenamiento seguro;
- [ ] registrar la herramienta LTI en CAMPUS;
- [ ] validar OIDC, Deep Linking, NRPS y AGS contra una instancia Moodle/CAMPUS real;
- [ ] realizar un piloto con alumnado real.

---

## 12. Desarrollo local

```bash
cp .env.example .env
docker compose up --build
```

Servicios:

- LMS: `http://localhost:8080`
- Health: `http://localhost:8080/api/health`
- JWKS LTI: `http://localhost:8080/lti/jwks`

---

## 13. Estructura del repositorio

```text
backend/       API, modelos, evaluación, LTI y runtime
frontend/      interfaz docente, alumno y reproductor SCORM
nginx/         proxy y contenido estático
docs/          documentación técnica y operativa
scripts/       instalación, actualización, backup, restore y validadores
modules/       manifiestos de módulos oficiales
downloads/     artefactos publicables, incluidos SCORM finales
storage/       almacenamiento local no versionado
deploy/        plantillas de despliegue desatendido
```

---

## 14. Documentación

| Documento | Contenido |
|---|---|
| `docs/RELEASE_1.0.0-RC2.md` | Estado de la release candidata y criterios de promoción |
| `docs/BLACKBOARD_ULTRA_ROADMAP.md` | Evolución funcional y semáforo de verificación |
| `docs/CAMPUS_LTI.md` | Integración LTI 1.3 Advantage |
| `docs/TEACHER_WORKSPACE.md` | Grupos, módulos, permisos y espacio docente |
| `docs/SCORM_EDITOR.md` | Edición, revisiones, multimedia y exportación SCORM |
| `docs/GTH_MIGRATION.md` | Estado de GTH, bancos privados y evaluación |
| `docs/NOMINASOL_GUIDED_PROJECT.md` | Proyecto guiado NOMINASOL |
| `deploy/unattended.env.example` | Plantilla de instalación automatizada |

---

## 15. Proyecto de origen

El primer módulo migrado procede de:

```text
atreyu1968/CFGSAF
commit: 6eaa240ce13a69e328c5b8d11ae2bdca99be7d17
ruta: grh0652/
```

La migración utiliza un commit fijado para impedir que cambios posteriores en el proyecto de origen modifiquen silenciosamente el contenido importado.

---

## 16. Límites de la release candidata

La versión actual es **1.0.0-rc2**.

El software propio está preparado para despliegue y validación real. Sin embargo, el repositorio no puede completar por sí solo:

- el alta institucional de la herramienta en CAMPUS del Gobierno de Canarias;
- la concesión de `client_id`, `deployment_id` y servicios LTI;
- la validación con credenciales reales de Moodle/CAMPUS;
- la carga de bancos privados que deliberadamente no se publican en Git.

La promoción a **1.0.0 estable** debe realizarse después de superar el piloto real LTI/NRPS/AGS/Deep Linking sin incidencias bloqueantes.
