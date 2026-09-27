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
│  ├─ motor SCORM 1.2                       │
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
5. **SCORM genérico**: subida, versionado y ejecución de paquetes SCORM, inicialmente SCORM 1.2.
6. **Evaluación trazable**: intentos, evidencias, correcciones, ajustes y notas quedan auditados.
7. **Sin secretos en Git**: claves LTI, API de IA, bancos privados y datos personales quedan fuera del repositorio.
8. **GTH se migra sin romperlo**: `CFGSAF` continúa siendo la versión estable de origen mientras se valida la migración.

## Proyecto guiado NOMINASOL 2026

Se ha incorporado un proyecto profesional guiado para **TeamSystem NOMINASOL 2026 · Versión Educativa**. La versión 2026.5 combina expediente anual, variantes individuales, auditoría técnica oculta, ayuda diagnóstica contextual, glosario, capturas ampliables y un portafolio de evolución con todos los intentos y correcciones. No se plantea como un manual esquemático: el alumno recibe contexto empresarial, explicación del porqué de cada operación, recorrido visual con capturas reales de la aplicación, comprobaciones antes de entregar y evidencias de progreso.

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
- [ ] Migración completa de GTH.
- [x] Reproductor SCORM 1.2 y SCORM 2004 con subida, registro y persistencia.
- [x] Editor SCORM versionado: los intentos iniciados quedan fijados a su revisión.
- [x] Exportación individual, de borradores y de toda la biblioteca SCORM.
- [x] Biblioteca multimedia para vídeo, audio, imágenes y PDF con streaming HTTP Range.
- [ ] Panel docente completo (la API multi-profesor y permisos ya está implementada).
- [x] Proyecto guiado NOMINASOL 2026 con hitos, evidencias, IA y capturas reales oficiales.
- [x] AGS implementado para retorno de notas; pendiente validación contra Moodle/CAMPUS.
- [x] NRPS implementado con paginación; pendiente validación contra Moodle/CAMPUS.
- [x] Deep Linking implementado y cubierto por pruebas locales.
- [ ] Instalador Ubuntu + Cloudflare Tunnel.
- [ ] Piloto real en CAMPUS.

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
