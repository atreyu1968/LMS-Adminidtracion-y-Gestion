# Release 1.0.0-rc1 — estado operativo

## Alcance

La versión **1.0.0-rc1** representa la primera release candidata operativa del LMS Administración y Gestión.

El término *release candidata* es deliberado:

- el código bajo control del repositorio está cubierto por CI;
- existe instalación Ubuntu reproducible;
- existe autodiagnóstico interno;
- las interfaces principales pasan validación JavaScript/HTML;
- las pruebas locales cubren LTI 1.3, Deep Linking, NRPS y AGS;
- todavía faltan dos validaciones que no pueden ejecutarse sin infraestructura externa: un Moodle/CAMPUS real y el alta institucional en CAMPUS Canarias.

## Instalación nueva

En un Ubuntu limpio:

```bash
git clone https://github.com/atreyu1968/LMS-Adminidtracion-y-Gestion.git
cd LMS-Adminidtracion-y-Gestion
sudo bash scripts/install-ubuntu.sh --url https://lms.tudominio.es
```

Si se dispone de token de un Cloudflare Tunnel ya creado:

```bash
sudo bash scripts/install-ubuntu.sh \
  --url https://lms.tudominio.es \
  --cloudflare-token 'TOKEN_DEL_TUNEL'
```

El hostname configurado en Cloudflare debe dirigir el túnel al servicio interno:

```text
http://web:8080
```

## Verificación

```bash
bash scripts/verify-installation.sh
```

La verificación exige:

1. respuesta de `/api/health`;
2. JWKS LTI disponible;
3. autodiagnóstico interno `code_ready=true`;
4. contenedores activos;
5. configuración Nginx válida.

Autodiagnóstico manual:

```bash
curl -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/admin/readiness
```

### Estados

- `code_ready=true`: base de datos, almacenamiento, claves, secretos, catálogo y motores internos están preparados.
- `campus_ready=true`: además existe URL HTTPS y una plataforma LTI registrada.

La autorización administrativa de CAMPUS no puede verificarse desde este endpoint.

## Copia de seguridad

```bash
bash scripts/backup.sh
```

Guarda:

- dump PostgreSQL;
- volumen `/data`;
- `.env` protegido;
- versión;
- checksums SHA-256.

El directorio de copia contiene secretos y debe almacenarse con acceso restringido.

## Catálogo inicial

El instalador intenta aprovisionar GTH y NOMINASOL. También puede hacerse manualmente:

```bash
curl -X POST -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/admin/catalog/gth0652/provision

curl -X POST -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/admin/catalog/nominasol2026/provision
```

## GTH

La migración pública queda integrada:

- 4 RA;
- 33 CE;
- 198 actividades públicas de Portafolio;
- SCORM públicos;
- motor de evaluación;
- recuperación;
- examen seguro;
- IA personal por profesor;
- AGS por RA.

### Dependencia privada

Las claves reales de Portafolio, examen y recuperación no están en Git. Deben cargarse desde el almacenamiento seguro del proyecto.

Esto no es un fallo del LMS: es una medida de seguridad deliberada para no publicar solucionarios.

## Funciones docentes cerradas

- Activity Stream global.
- Cola de revisión transversal.
- Gradebook RA/CE.
- detalle de alumno.
- matriz de progreso.
- fechas de disponibilidad.
- prerrequisitos y puntuación mínima.
- secuencia RA y secuencia de actividades.
- excepciones individuales.
- exenciones.
- búsqueda.
- favoritos y recientes.
- notificaciones.
- calendario.
- actividad observable.
- IA personal cifrada.

## Funciones del alumnado cerradas

- progreso global;
- progreso RA;
- estados por actividad;
- nota por RA y CE;
- siguiente paso;
- índice lateral;
- anterior/siguiente;
- reanudación;
- bloqueos de acceso;
- exenciones visibles;
- búsqueda contextual;
- calendario;
- notificaciones;
- cronología personal.

## CI

La pipeline comprueba:

- compilación Python;
- tests funcionales;
- LTI/AGS/NRPS/Deep Linking;
- SCORM 1.2/2004;
- edición/versionado/exportación;
- multimedia/HTTP Range;
- evaluación/IA;
- liberación adaptativa;
- experiencia docente/alumno;
- autodiagnóstico;
- sintaxis de scripts de despliegue;
- Docker Compose;
- JavaScript de los frontends;
- IDs/selectores básicos;
- landmarks esenciales de accesibilidad.

## Condiciones para 1.0.0 definitiva

Se publicará como 1.0.0 cuando se hayan completado:

1. validación contra una instancia Moodle externa controlada;
2. registro LTI en CAMPUS Canarias;
3. prueba de OIDC/launch real;
4. sincronización NRPS real;
5. envío/lectura AGS real;
6. Deep Linking real desde el selector de CAMPUS;
7. piloto con un grupo;
8. carga/validación de bancos privados de GTH si GTH forma parte del piloto.

Hasta entonces, **1.0.0-rc1 es operativa para despliegue y pruebas, pero no debe presentarse como integración CAMPUS institucionalmente certificada**.
