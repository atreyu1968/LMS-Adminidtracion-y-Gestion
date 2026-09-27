# Release 1.0.0-rc2 — candidata operativa

## Alcance

**1.0.0-rc2** es la candidata operativa del LMS Administración y Gestión posterior al cierre del roadmap funcional inspirado en Blackboard Learn Ultra y al endurecimiento de la operación en servidor.

La release separa expresamente dos conceptos:

- **operatividad interna verificable**: todo lo que depende del código, contenedores, base de datos, almacenamiento y pruebas del repositorio;
- **certificación CAMPUS**: validación contra una plataforma Moodle/CAMPUS real y alta institucional, que depende de infraestructura y credenciales externas.

No se presentará como versión 1.0.0 definitiva hasta completar el segundo bloque.

## Funcionalidad interna cerrada

### Docencia y grupos

- multi-profesor;
- grupos locales y grupos CAMPUS;
- profesores colaboradores;
- alumnado individual o CSV;
- autoinscripción opcional por código;
- catálogo de módulos oficiales;
- copias personales editables.

### Experiencia docente

- centro global de actividad;
- cola transversal de revisión;
- Gradebook por RA/CE;
- detalle integral de alumno;
- matriz de progreso;
- actividad observable;
- búsqueda;
- favoritos y recientes;
- notificaciones;
- calendario;
- reglas de disponibilidad;
- secuenciación;
- excepciones;
- exenciones.

### Experiencia del alumnado

- progreso global y por RA;
- estados por actividad;
- notas RA/CE;
- próxima acción;
- índice de RA;
- anterior/siguiente;
- reanudación;
- bloqueos por condiciones;
- exenciones visibles;
- búsqueda contextual;
- avisos y calendario;
- cronología de actividad.

### SCORM

- SCORM 1.2 y SCORM 2004;
- audio y vídeo;
- HTTP Range para multimedia;
- biblioteca docente;
- edición visual/código;
- revisiones inmutables;
- preservación de intentos y notas;
- copia editable de material compartido;
- exportación individual, borrador y biblioteca completa.

### Evaluación FP

- RA y CE;
- Portafolio;
- examen seguro;
- recuperación selectiva;
- intentos;
- bancos privados;
- revisión docente;
- IA personal del profesor;
- devolución AGS por RA definitivo.

### CAMPUS / LTI

Implementado y cubierto por pruebas locales:

- LTI 1.3 Core/OIDC;
- Deep Linking;
- NRPS;
- AGS;
- identidad federada;
- vinculación curso/grupo;
- columna calificable por módulo o RA.

## Operación del servidor

### Instalación nueva

```bash
git clone https://github.com/atreyu1968/LMS-Adminidtracion-y-Gestion.git
cd LMS-Adminidtracion-y-Gestion
sudo bash scripts/install-ubuntu.sh --url https://lms.tudominio.es
```

Con Cloudflare Tunnel:

```bash
sudo bash scripts/install-ubuntu.sh \
  --url https://lms.tudominio.es \
  --cloudflare-token 'TOKEN_DEL_TUNEL'
```

### Actualización segura

```bash
sudo bash scripts/update-ubuntu.sh
```

El actualizador:

1. crea una copia previa;
2. actualiza el repositorio mediante fast-forward;
3. reconstruye contenedores;
4. aplica migraciones de esquema;
5. espera a que la API esté sana;
6. ejecuta la verificación completa;
7. conserva la ruta exacta del backup para rollback.

### Copia de seguridad

```bash
sudo bash scripts/backup.sh
```

Incluye:

- PostgreSQL;
- volumen de almacenamiento;
- secretos de configuración;
- versión;
- hashes SHA-256.

### Restauración

```bash
sudo bash scripts/restore.sh backups/FECHA --yes
```

La restauración:

- verifica hashes;
- recupera base de datos;
- recupera almacenamiento;
- restaura los secretos criptográficos necesarios;
- conserva la configuración de red/base del servidor destino;
- reconstruye servicios;
- aplica migraciones actuales;
- ejecuta la verificación final.

### Verificación

```bash
bash scripts/verify-installation.sh
```

También:

```bash
curl -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/admin/readiness
```

El autodiagnóstico comprueba:

- base de datos;
- versión de esquema;
- almacenamiento;
- clave LTI;
- secretos;
- catálogo;
- motor SCORM;
- motor de módulos;
- HTTPS;
- plataforma LTI registrada.

## Migraciones de base de datos

La aplicación mantiene una versión interna de esquema y ejecuta migraciones idempotentes al arrancar después de crear tablas nuevas.

La migración de compatibilidad cubre las primeras versiones del proyecto y preserva las filas existentes al incorporar:

- propiedad/configuración de grupos;
- relación LTI con RA;
- biblioteca y versionado SCORM;
- columnas de revisión/visibilidad necesarias.

La instalación de producción soportada utiliza PostgreSQL.

## CI de rc2

La pipeline incluye dos niveles.

### Batería funcional

- compilación Python;
- pruebas API;
- SCORM 1.2/2004;
- multimedia;
- edición/versionado;
- evaluación;
- IA;
- GTH;
- NOMINASOL;
- LTI/NRPS/AGS/Deep Linking;
- liberación adaptativa;
- excepciones/exenciones;
- experiencia docente/alumno;
- accesibilidad base;
- migración de esquema.

### Stack completo

La CI levanta realmente:

```text
PostgreSQL 17
   ↓
FastAPI
   ↓
Nginx
```

y comprueba:

- healthchecks;
- JWKS;
- autodiagnóstico;
- Nginx;
- backup;
- restore;
- arranque posterior al restore;
- diagnóstico final.

## GTH

El contenido público migrado contiene:

- 4 RA;
- 33 CE;
- 198 actividades públicas;
- SCORM;
- evaluación;
- examen;
- recuperación;
- IA por profesor;
- AGS por RA.

Las claves reales de Portafolio/examen/recuperación siguen deliberadamente fuera de Git. Se cargan desde almacenamiento seguro antes de utilizar GTH en un piloto real.

## Límites que no puede cerrar el repositorio

### CAMPUS real

Queda por realizar con una plataforma externa:

1. alta de la herramienta;
2. OIDC/launch real;
3. Deep Linking real;
4. NRPS real;
5. AGS real;
6. comprobación de roles/contextos;
7. piloto con alumnado.

### Registro institucional

El deployment/client y permisos de CAMPUS deben ser concedidos por su administración. El LMS no puede autoautorizarse.

## Criterio para promover a 1.0.0

La release puede promoverse a **1.0.0** cuando:

- el stack interno siga verde;
- se registre la herramienta en CAMPUS;
- se complete el ciclo real LTI/NRPS/AGS/Deep Linking;
- se ejecute un piloto controlado;
- se carguen los bancos privados necesarios para los módulos del piloto;
- no aparezcan incidencias bloqueantes en el piloto.

Hasta entonces, **1.0.0-rc2 es una candidata operativa para despliegue y validación real, no una certificación institucional de CAMPUS**.
