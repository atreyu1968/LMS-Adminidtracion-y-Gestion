# Plan de migración de GTH/0652

## Fuente

Repositorio estable:

```text
atreyu1968/CFGSAF
└── grh0652/
```

La migración no modifica esa aplicación hasta que el nuevo LMS esté validado.

## Inventario que se conserva

- 4 RA/UT activos;
- teoría e infografías;
- prácticas guiadas;
- Portafolio;
- examen seguro;
- recuperación por CE;
- revisión docente/IA;
- rectificaciones;
- exportación Additio;
- bancos privados;
- trazabilidad y auditoría;
- SCORM 1.2.

## Transformaciones

### Identidad
Antes: token personal propio de GTH.

Después:
- en CAMPUS: `issuer + sub` LTI;
- modo local/piloto: cuenta administrativa explícita;
- el token GTH se mantiene únicamente durante la compatibilidad transitoria.

### Cursos
Antes: `course_id` identifica RA/UT.

Después:
- `Module`: plantilla didáctica GTH/0652;
- `Course`: aula concreta procedente de CAMPUS;
- `CourseModule`: GTH activado dentro de esa aula;
- RA/UT pasa a ser contenido/configuración del módulo, no un curso global.

### Profesores
Antes: un único `GRH_TEACHER_TOKEN`.

Después:
- múltiples usuarios docentes;
- membresía por curso;
- roles recibidos por LTI;
- privilegios locales auditados.

### Base de datos
Antes: SQLite monolítico.

Después: PostgreSQL con entidades normalizadas y migraciones.

### Calificaciones
Antes: panel interno + exportación Additio.

Después:
- cálculo/evidencias internas;
- sincronización AGS con CAMPUS;
- Additio queda como exportación opcional.

## Orden de trabajo

1. Congelar referencia funcional de `CFGSAF/grh0652`.
2. Importar contenido público.
3. Crear adaptador de SCORM heredado.
4. Migrar estado/evidencias.
5. Migrar Portafolio.
6. Migrar examen seguro.
7. Migrar recuperación.
8. Migrar IA y rúbricas.
9. Mapear notas a AGS.
10. Probar con dos profesores y dos cursos.
11. Probar contra Moodle de laboratorio.
12. Solicitar/realizar piloto CAMPUS.
