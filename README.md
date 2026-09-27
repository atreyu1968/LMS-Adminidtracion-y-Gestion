# LMS Administración y Gestión

LMS modular para la familia profesional de **Administración y Gestión**, construido a partir de la experiencia de `GRH0652` del repositorio `atreyu1968/CFGSAF`.

## Objetivo

El proyecto separa el motor LMS de los contenidos de cada módulo profesional. La misma instalación podrá alojar, entre otros:

- Gestión de Recursos Humanos (GTH / 0652), como primer módulo migrado.
- Módulos del CFGM de Gestión Administrativa.
- Otros módulos y cursos SCORM incorporados posteriormente por profesorado autorizado.

El sistema está diseñado para que **CAMPUS (Moodle del Gobierno de Canarias) sea la puerta de entrada principal** y, cuando la administración de CAMPUS autorice el registro de la herramienta, también el libro de calificaciones principal.

## Integración con CAMPUS

La integración prevista es **LTI 1.3 Advantage**:

- **LTI Core 1.3 + OIDC**: acceso desde CAMPUS sin una segunda contraseña.
- **NRPS (Names and Role Provisioning Services)**: sincronización de alumnado, profesorado y roles del aula.
- **AGS (Assignment and Grade Services)**: devolución de calificaciones al calificador de CAMPUS.
- **Deep Linking**: permite que el profesorado seleccione desde CAMPUS qué módulo, RA/UT o actividad del LMS quiere insertar.

CAMPUS está basado en Moodle. Moodle soporta herramientas externas LTI 1.3 Advantage. La activación definitiva en CAMPUS requerirá que su administración registre este LMS como herramienta externa y conceda los servicios necesarios.

## Arquitectura

```text
CAMPUS / Moodle
    │
    │ LTI 1.3 Advantage
    ▼
┌────────────────────────────────────────────┐
│ LMS Administración y Gestión              │
│                                            │
│  FastAPI                                   │
│  ├─ identidad LTI / sesiones              │
│  ├─ centros, cursos, profesores, alumnos  │
│  ├─ catálogo de módulos                   │
│  ├─ motor SCORM 1.2 / 2004                │
│  ├─ evaluación / evidencias               │
│  ├─ AGS → calificaciones a CAMPUS         │
│  └─ NRPS → matrículas y roles             │
│                                            │
│  PostgreSQL        Almacenamiento SCORM    │
└────────────────────────────────────────────┘
```

## Principios de diseño

1. **Multi-módulo**: el núcleo no conoce GTH de forma rígida.
2. **Multi-profesor**: cada curso puede tener uno o varios docentes.
3. **Multi-centro**: preparado para varias organizaciones/centros si se necesitara.
4. **CAMPUS-first**: en producción, la identidad del alumno procede del lanzamiento LTI.
5. **SCORM genérico**: subida, versionado y ejecución de paquetes SCORM 1.2 y SCORM 2004.
6. **Evaluación trazable**: intentos, evidencias, correcciones, ajustes y notas quedan auditados.
7. **Sin secretos en Git**: claves LTI, API de IA, bancos privados y datos personales quedan fuera del repositorio.
8. **GTH se migra sin romperlo**: `CFGSAF` continúa siendo la versión estable de origen mientras se valida la migración.

## Proyecto guiado NOMINASOL 2026

Se ha incorporado un proyecto profesional guiado para **TeamSystem NOMINASOL 2026 · Versión Educativa**. La versión 2026.10 añade al inicio del SCORM el acceso oficial para descargar/solicitar NOMINASOL 2026 Educativa para Windows, manteniendo la empresa maestra privada, el expediente documental y la auditoría por hitos. No se plantea como un manual esquemático: el alumno recibe contexto empresarial, explicación del porqué de cada operación, recorrido visual con capturas reales de la aplicación, comprobaciones antes de entregar y evidencias de progreso.

El flujo es:

    situación profesional
    → explicación didáctica
    → operación en NOMINASOL
    → autocontrol
    → evidencia
    → comprobación IA / docente
    → corrección si procede
    → hito superado
    → siguiente fase

La IA utiliza la API configurada por el profesor y puede validar capturas de pantalla. Los hitos críticos o de baja confianza quedan pendientes de revisión humana. Las evidencias nunca sustituyen versiones anteriores: se conserva la evolución completa.

Documentación: docs/NOMINASOL_GUIDED_PROJECT.md.

Para aprovisionar el módulo oficial después de desplegar:

    curl -X POST \\
      -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \\
      "$LMS_PUBLIC_BASE_URL/api/admin/catalog/nominasol2026/provision"

## Estado

### Fase 0 — base del nuevo LMS
- [x] Repositorio independiente.
- [x] Diseño multi-módulo y multi-profesor.
- [x] PostgreSQL + FastAPI + Docker.
- [x] LTI 1.3 Core: OIDC, JWKS, lanzamiento, identidad y roles.
- [x] Modelo de datos inicial.
- [x] Biblioteca SCORM personal por profesor, con visibilidad privada/compartida.
- [x] Grupos locales propios y cursos CAMPUS unificados en el mismo modelo.
- [x] Importación de alumnado por CSV y profesores colaboradores.
- [x] Panel docente para grupos, módulos y biblioteca SCORM.
- [x] Migración funcional pública de GTH: 4 RA, 33 CE, 198 actividades, SCORM y motor de evaluación.
- [ ] Carga de bancos privados reales de GTH desde almacenamiento seguro (dependencia externa al repositorio).
- [x] Reproductor SCORM 1.2 y SCORM 2004 con subida, registro y persistencia.
- [x] Editor SCORM versionado: los intentos iniciados quedan fijados a su revisión.
- [x] Exportación individual, de borradores y de toda la biblioteca SCORM.
- [x] Biblioteca multimedia para vídeo, audio, imágenes y PDF con streaming HTTP Range.
- [x] Panel docente completo: actividad global, grupos, módulos, catálogo, SCORM, multimedia, IA, evaluación, progreso y disponibilidad.
- [x] Proyecto guiado NOMINASOL 2026 con hitos, evidencias, IA y capturas reales oficiales.
- [x] AGS implementado para retorno de notas; pendiente validación contra Moodle/CAMPUS.
- [x] NRPS implementado con paginación; pendiente validación contra Moodle/CAMPUS.
- [x] Deep Linking implementado y cubierto por pruebas locales.
- [x] Instalador Ubuntu + Docker Compose + Cloudflare Tunnel opcional + autodiagnóstico.
- [ ] Piloto real en CAMPUS.

## Instalación desatendida en Ubuntu

La vía recomendada para producción es el **bootstrap desatendido**. Puede ejecutarse sobre un servidor Ubuntu limpio: instala las dependencias mínimas, clona este repositorio, crea los secretos, instala/activa Docker, levanta PostgreSQL + FastAPI + Nginx, habilita Cloudflare Tunnel si se proporciona un token, aprovisiona el catálogo y ejecuta el autodiagnóstico final.

### Requisitos previos

- servidor Ubuntu con `sudo`/root;
- salida a Internet para APT, GitHub, Docker Hub y, si se utiliza, Cloudflare;
- una URL pública HTTPS, por ejemplo `https://lms.midominio.es`;
- opcionalmente, un token de Cloudflare Tunnel ya creado.

No es necesario instalar previamente Git, Docker, Docker Compose, PostgreSQL, Python ni Nginx.

### Opción A — una sola orden

Sin Cloudflare gestionado por esta instalación:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  | sudo bash -s -- --url https://lms.midominio.es
```

El sistema queda instalado por defecto en:

```text
/opt/lms-administracion-y-gestion
```

Si el dominio/túnel ya está gestionado externamente, basta con hacer que el proxy/túnel dirija el tráfico HTTPS hacia:

```text
http://127.0.0.1:8080
```

### Opción B — desatendida con Cloudflare Tunnel

Es preferible guardar el token fuera del historial del shell:

```bash
sudo install -m 600 /dev/null /root/cloudflare-lms.token
sudo nano /root/cloudflare-lms.token
```

Pega únicamente el token del túnel, guarda el fichero y ejecuta:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  -o /tmp/lms-bootstrap.sh

sudo bash /tmp/lms-bootstrap.sh \
  --url https://lms.midominio.es \
  --cloudflare-token-file /root/cloudflare-lms.token
```

Cuando se utiliza el contenedor `cloudflared`, el hostname público del túnel debe apuntar al servicio interno:

```text
http://web:8080
```

No a `localhost:8080` dentro de la configuración del contenedor.

### Opción C — fichero de configuración para cloud-init/Ansible

El repositorio incluye:

```text
deploy/unattended.env.example
```

Ejemplo de fichero `/root/lms-install.env`:

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

Y ejecuta el bootstrap sin interacción:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  -o /tmp/lms-bootstrap.sh

sudo bash /tmp/lms-bootstrap.sh --config /root/lms-install.env
```

Este formato es adecuado para aprovisionamiento automatizado mediante cloud-init, Ansible u otra herramienta de despliegue.

### Qué realiza automáticamente

El proceso:

1. instala `ca-certificates`, `curl`, `git`, Docker y Docker Compose;
2. clona el LMS en el directorio indicado;
3. genera aleatoriamente contraseña PostgreSQL, secreto de sesión, token administrativo y secreto de cifrado de IA;
4. crea `.env` con permisos `600`;
5. genera la clave RSA de la herramienta LTI dentro del volumen persistente;
6. construye y levanta PostgreSQL, FastAPI y Nginx;
7. levanta `cloudflared` cuando existe un token de túnel;
8. aplica automáticamente las migraciones de base de datos;
9. aprovisiona GTH y NOMINASOL salvo que se indique `--no-provision`;
10. comprueba salud, versión, JWKS, esquema, almacenamiento, Nginx y autodiagnóstico;
11. deja un informe de instalación en:

```text
/var/log/lms-administracion-y-gestion-install.txt
```

El informe no contiene contraseñas ni tokens.

### Desactivar el aprovisionamiento inicial

Para instalar solo el motor LMS:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/atreyu1968/LMS-Adminidtracion-y-Gestion/main/scripts/bootstrap-ubuntu.sh \
  | sudo bash -s -- \
      --url https://lms.midominio.es \
      --no-provision
```

### Verificación después de instalar

El instalador ya ejecuta esta comprobación. Puede repetirse en cualquier momento:

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

`/api/health` debe devolver `"ok": true` y la misma versión indicada en `VERSION`.

### Consultar la configuración administrativa

Los secretos no se muestran durante la instalación. Para consultar el token administrativo desde el propio servidor:

```bash
sudo grep '^LMS_ADMIN_TOKEN=' /opt/lms-administracion-y-gestion/.env
```

No publiques ni copies el contenido completo de `.env`.

### Una instalación ya existe

El bootstrap **no sobrescribe** una instalación existente. Si encuentra un repositorio en el directorio destino, se detiene deliberadamente.

Para actualizar:

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/update-ubuntu.sh
```

La actualización crea primero un backup, hace `git pull --ff-only`, reconstruye los contenedores, aplica las migraciones y vuelve a verificar el sistema.

### Backup y restauración

Copia manual:

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/backup.sh
```

Restauración:

```bash
cd /opt/lms-administracion-y-gestion
sudo bash scripts/restore.sh backups/FECHA --yes
```

La copia incluye PostgreSQL, almacenamiento persistente, secretos necesarios, versión y hashes de integridad.

### Diagnóstico rápido

```bash
cd /opt/lms-administracion-y-gestion
set -a
source .env
set +a

curl -fsS \
  -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/admin/readiness
```

`code_ready: true` confirma que la instalación interna está preparada.

`campus_ready` permanecerá en `false` hasta que exista una URL HTTPS pública válida y se haya registrado una plataforma LTI Moodle/CAMPUS.

### Ficheros importantes

```text
/opt/lms-administracion-y-gestion/.env
    secretos y configuración; permisos 600

/var/log/lms-administracion-y-gestion-install.txt
    informe de la última instalación

/opt/lms-administracion-y-gestion/backups/
    copias realizadas antes de actualizaciones o manualmente

deploy/unattended.env.example
    plantilla para instalaciones automatizadas
```

## Arranque local

```bash
cp .env.example .env
docker compose up --build
```

Después:

- LMS: `http://localhost:8080`
- API de salud: `http://localhost:8080/api/health`
- JWKS LTI: `http://localhost:8080/lti/jwks`

## Estructura

```text
backend/             API y motor LMS
frontend/            interfaz web
nginx/               proxy y contenido estático
docs/                arquitectura, integración CAMPUS y espacio docente
scripts/             migraciones/importadores
modules/             manifiestos de módulos propios
storage/             volumen local (no versionado)
```

## Proyecto de origen

La primera migración se realizará desde:

```text
https://github.com/atreyu1968/CFGSAF/tree/6eaa240ce13a69e328c5b8d11ae2bdca99be7d17/grh0652
```

La aplicación original seguirá siendo la referencia funcional de GTH hasta que la nueva versión supere sus pruebas de regresión.


## Espacio docente

La arquitectura y el flujo de trabajo de grupos propios, profesores colaboradores y biblioteca SCORM están documentados en `docs/TEACHER_WORKSPACE.md`.

El panel docente se sirve en:

```text
/teacher.html
```


## Edición y exportación SCORM

El funcionamiento del editor, el versionado no destructivo, la biblioteca multimedia y la exportación están documentados en `docs/SCORM_EDITOR.md`.


## Migración de GTH

El estado, las reglas conservadas, el aprovisionamiento del catálogo y la carga separada de bancos privados se documentan en `docs/GTH_MIGRATION.md`.


## Release candidata 1.0.0-rc2

La versión actual es **1.0.0-rc2**.

El software bajo control del repositorio se encuentra operativo y cubierto por CI funcional y por una prueba del stack Docker completo. La documentación de instalación, autodiagnóstico, copia de seguridad y criterios para promover la release a 1.0.0 definitiva está en:

`docs/RELEASE_1.0.0-RC2.md`

Instalación Ubuntu:

```bash
sudo bash scripts/install-ubuntu.sh --url https://lms.tudominio.es
```

Verificación:

```bash
bash scripts/verify-installation.sh
```

Actualización segura:

```bash
sudo bash scripts/update-ubuntu.sh
```

Copia de seguridad:

```bash
sudo bash scripts/backup.sh
```

Restauración:

```bash
sudo bash scripts/restore.sh backups/FECHA --yes
```

### Lo que aún depende de terceros

El código no puede completar por sí solo:

- el registro LTI de la herramienta en CAMPUS del Gobierno de Canarias;
- la validación contra una instancia Moodle/CAMPUS real con credenciales concedidas;
- la carga de bancos privados de GTH que deliberadamente no se publican en Git.

Estos elementos se mantienen separados del estado de calidad interna del LMS.
