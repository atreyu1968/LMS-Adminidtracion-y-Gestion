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

## Estado

### Fase 0 — base del nuevo LMS
- [x] Repositorio independiente.
- [x] Diseño multi-módulo y multi-profesor.
- [x] PostgreSQL + FastAPI + Docker.
- [x] Esqueleto LTI 1.3.
- [x] Modelo de datos inicial.
- [ ] Migración completa de GTH.
- [ ] Reproductor SCORM genérico con persistencia.
- [ ] Panel docente.
- [ ] AGS completo y probado contra Moodle.
- [ ] NRPS completo y probado contra Moodle.
- [ ] Deep Linking.
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
docs/                arquitectura e integración CAMPUS
scripts/             migraciones/importadores
modules/             manifiestos de módulos propios
storage/             volumen local (no versionado)
```

## Proyecto de origen

La primera migración se realizará desde:

```text
https://github.com/atreyu1968/CFGSAF/tree/main/grh0652
```

La aplicación original seguirá siendo la referencia funcional de GTH hasta que la nueva versión supere sus pruebas de regresión.
