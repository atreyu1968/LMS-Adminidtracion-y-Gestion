# Editor SCORM, revisiones y exportación

## Objetivo

Permitir que el profesorado importe, edite, reutilice y exporte paquetes SCORM sin comprometer los intentos, evidencias ni calificaciones ya guardadas.

## Principio de seguridad académica

Una revisión publicada que ya haya sido iniciada por un alumno se considera **inmutable para ese intento**.

Cuando el profesor modifica un SCORM:

1. el LMS crea un borrador de trabajo;
2. el contenido publicado anterior no se modifica;
3. el profesor edita el borrador;
4. al publicar, el LMS crea una revisión nueva;
5. las asignaciones de los módulos que pertenecen al profesor pasan a la nueva revisión;
6. los alumnos sin intento previo reciben la revisión nueva;
7. los alumnos con intento previo permanecen vinculados a la revisión que iniciaron;
8. sus puntuaciones, estado, ubicación y `suspend_data` permanecen intactos.

La edición nunca ejecuta una migración automática de calificaciones entre revisiones.

## Ejemplo

```text
SCORM Nóminas
├─ revisión 1
│  ├─ Ana: 73/100, completado
│  └─ Luis: en progreso
│
└─ revisión 2  ← revisión actual
   ├─ Marta: nuevo intento
   └─ Pablo: nuevo intento
```

Ana y Luis continúan abriendo la revisión 1. Marta y Pablo reciben la revisión 2.

## SCORM compartidos

Un SCORM compartido puede ser reutilizado por otros profesores, pero el profesor receptor no modifica el original.

La acción **Crear copia editable** genera un nuevo SCORM privado en su biblioteca. A partir de ese momento el profesor puede editarlo, publicarlo y exportarlo independientemente.

Cuando el propietario del SCORM compartido publica una nueva revisión, los módulos de otros profesores que estaban usando una revisión anterior no se actualizan silenciosamente.

## Editor

El editor está disponible desde **Mi biblioteca SCORM → Editar**.

Incluye:

- explorador de archivos;
- edición de HTML, CSS, JavaScript, JSON, XML, texto, SVG, subtítulos VTT/SRT y otros archivos de texto;
- modo de edición visual rápida para los textos de páginas HTML;
- sustitución de archivos;
- incorporación de archivos nuevos;
- eliminación controlada de archivos;
- protección frente a eliminación de `imsmanifest.xml`;
- vista previa aislada;
- edición de título, descripción y visibilidad;
- biblioteca multimedia integrada;
- historial de revisiones;
- exportación del borrador;
- publicación de una nueva revisión.

## Biblioteca multimedia

Admite:

- vídeo;
- audio;
- imágenes;
- PDF.

Los recursos pueden ser privados o compartidos.

Cuando un recurso multimedia se inserta desde la biblioteca en un SCORM, se **copia físicamente dentro del borrador**. Esto garantiza que el ZIP exportado siga siendo autónomo y pueda ejecutarse en otro LMS sin depender de esta instalación.

## Vídeo y audio

Los ficheros multimedia almacenados en la biblioteca se sirven mediante un endpoint autenticado que soporta peticiones HTTP Range.

Esto permite:

- reproducción progresiva;
- avanzar y retroceder;
- evitar descargar el fichero completo antes de comenzar;
- mantener privados los recursos del profesor.

Los recursos incorporados a un SCORM pasan a formar parte del paquete y se sirven con el contenido SCORM.

## Exportación

### Exportar un SCORM

Cada revisión puede descargarse como ZIP SCORM válido.

### Exportar un borrador

El profesor puede descargar el borrador antes de publicarlo. Antes de exportarlo, el servidor valida que:

- existe `imsmanifest.xml`;
- el manifiesto es XML válido;
- existe un recurso de lanzamiento;
- el recurso de lanzamiento está presente en el paquete.

### Exportar toda la biblioteca

La acción **Exportar todos** genera:

```text
biblioteca-scorm.zip
├─ catalogo.json
└─ scorm/
   ├─ paquete-1-r2.zip
   ├─ paquete-2-r1.zip
   └─ ...
```

Cada ZIP interior sigue siendo un paquete SCORM independiente.

## Compatibilidad

Actualmente:

- SCORM 1.2 → `window.API`;
- SCORM 2004 → `window.API_1484_11`.

La revisión se detecta desde `imsmanifest.xml`.

## Límites configurables

Variables de entorno:

```text
LMS_MAX_SCORM_UPLOAD_MB=512
LMS_MAX_MEDIA_UPLOAD_MB=2048
```

Los valores pueden aumentarse en el servidor si el almacenamiento y el proxy lo permiten.

## Comportamiento de calificaciones

Las puntuaciones SCORM pertenecen al registro de intento, que referencia una revisión concreta mediante `package_id`.

La publicación de una revisión nueva:

- no actualiza registros de intentos anteriores;
- no modifica `score_raw`, `score_min` ni `score_max`;
- no modifica `lesson_status`;
- no modifica `suspend_data`;
- no toca las calificaciones AGS ya enviadas a CAMPUS.

Cualquier futura agregación por RA o CE deberá tratar todas las revisiones de una misma línea SCORM como el mismo recurso lógico, respetando la revisión fijada para cada intento.
