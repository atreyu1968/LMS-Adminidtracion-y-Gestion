# Evolución inspirada en Blackboard Learn Ultra

## Objetivo

Adoptar patrones maduros de experiencia docente y de alumnado de Blackboard Learn Ultra sin copiar su interfaz ni convertir el LMS en un producto genérico. La referencia se utiliza para mejorar:

- navegación;
- centro de actividad;
- cuaderno de calificaciones;
- progreso;
- secuenciación;
- condiciones de acceso;
- excepciones individuales;
- experiencia móvil;
- trazabilidad de evaluación.

La especialización del LMS se mantiene en Formación Profesional, RA/CE, SCORM editable, IA por profesor, recuperación por criterios y conexión con CAMPUS mediante LTI 1.3 Advantage.

## Semáforo global

Leyenda:

- 🟢 implementado y cubierto por CI;
- 🟡 implementado parcialmente o en desarrollo;
- 🔴 pendiente;
- ⚪ depende de integración externa o prueba en CAMPUS.

| ID | Área | Tarea | Estado | Criterio de verificación |
|---|---|---|---|---|
| BB-01 | Inicio docente | Centro global de actividad con trabajo pendiente de todos los grupos | 🟢 | API agregada + panel con revisiones, recuperación, notas CAMPUS pendientes y actividad SCORM |
| BB-02 | Gradebook | Cuaderno por alumno × RA con estado, revisión y nota | 🟢 | `/evaluation-teacher.html` muestra todos los alumnos y RA |
| BB-03 | Gradebook | Panel lateral/detalle completo de una calificación | 🟢 | Respuesta, intentos, CE, propuesta IA, feedback, historial y nota final en una sola vista |
| BB-04 | Evaluación | Cola global “Listo para corregir” entre todos los grupos | 🟢 | El inicio docente permite entrar directamente a cada cola |
| BB-05 | CAMPUS | Deep Linking de módulo completo o RA | 🟢 | LTI Deep Linking firmado y probado |
| BB-06 | CAMPUS | AGS por RA solo con notas definitivas | 🟢 | CI comprueba que resultados provisionales no se envían |
| BB-07 | CAMPUS | Sincronización NRPS de participantes | 🟢 | Endpoint paginado y protegido |
| BB-08 | Progreso | Indicador No iniciado / En curso / Completado por contenido | 🟢 | Estado uniforme en SCORM, actividades y RA |
| BB-09 | Progreso | Barra de progreso de módulo/RA para alumnado | 🟢 | Porcentaje y contador de elementos completados |
| BB-10 | Progreso | Vista docente de progreso por alumno | 🟢 | Matriz alumnado × elementos con filtros |
| BB-11 | Contenidos | Navegación secuencial “Anterior / Siguiente” dentro del RA | 🟢 | TOC lateral y reanudación en último elemento |
| BB-12 | Condiciones | Fechas de apertura/cierre por contenido | 🟢 | No accesible fuera de ventana y visible según configuración |
| BB-13 | Condiciones | Prerrequisitos por finalización | 🟢 | Regla “completa X antes de Y” |
| BB-14 | Condiciones | Prerrequisitos por puntuación | 🟢 | Regla por umbral de actividad/RA |
| BB-15 | Condiciones | Secuencia obligatoria dentro de un RA | 🟢 | Elementos posteriores bloqueados hasta completar el anterior |
| BB-16 | Condiciones | Liberación por alumno/grupo | 🟢 | Reglas individualizadas o por subconjunto |
| BB-17 | Excepciones | Intentos adicionales individuales | 🟢 | Excepción por alumno sin modificar configuración general |
| BB-18 | Excepciones | Ampliación de tiempo individual | 🟢 | Tiempo adicional persistente y auditado |
| BB-19 | Exenciones | Eximir actividad/CE con trazabilidad | 🟢 | La exención no penaliza cálculo y queda registrada |
| BB-20 | Alumno | Inicio de curso orientado a “qué hago ahora” | 🟢 | Próxima actividad, pendientes, progreso y notas |
| BB-21 | Alumno | Vista de calificaciones comprensible por RA/CE | 🟢 | La vista del alumno muestra estado, nota RA, desglose CE, recuperación y pendientes |
| BB-22 | Actividad | Registro temporal de acceso/inicio/envío | 🟢 | Intentos, exámenes y SCORM alimentan una cronología consolidada |
| BB-23 | Actividad | Informe de actividad del alumno | 🟢 | Cronología por alumno y evaluación |
| BB-24 | SCORM | Reproducción 1.2/2004, vídeo y audio | 🟢 | CI cubre runtime y HTTP Range |
| BB-25 | SCORM | Edición versionada sin alterar notas | 🟢 | Alumno iniciado permanece fijado a revisión anterior |
| BB-26 | SCORM | Exportación individual y biblioteca completa | 🟢 | ZIP SCORM válidos + catálogo |
| BB-27 | Evaluación FP | RA/CE, Portafolio, examen y recuperación | 🟢 | Motor genérico operativo |
| BB-28 | IA | API personal por profesor | 🟢 | Credencial cifrada, aislada y probada |
| BB-29 | IA | Revisión humana por confianza insuficiente | 🟢 | Cola docente conserva propuestas no aceptables automáticamente |
| BB-30 | Catálogo | Módulos oficiales + copia editable | 🟢 | Catálogo + fork personal |
| BB-31 | UX | Búsqueda de contenido dentro del módulo | 🟢 | Búsqueda por título/RA/CE/SCORM |
| BB-32 | UX | Favoritos y recientes para profesorado | 🟢 | Grupos/módulos fijables y últimos accesos |
| BB-33 | UX | Diseño responsive docente y alumno | 🟢 | Superficies principales usan layouts adaptativos y se validan en CI |
| BB-34 | Accesibilidad | Navegación por teclado, foco y ARIA | 🟢 | Cobertura base: salto a contenido, foco, landmarks/ARIA y validación estática; no equivale a certificación WCAG |
| BB-35 | Notificaciones | Avisos de revisión, recuperación y vencimientos | 🟢 | Centro de notificaciones configurable |
| BB-36 | Calendario | Fechas de actividades integradas en calendario | 🟢 | Vista calendario por grupo |
| BB-37 | Analítica | Riesgo por baja actividad/progreso | 🟢 | Señales transparentes, no diagnósticas, con datos observables |
| BB-38 | Integración | Validación real Moodle controlado | ⚪ | LTI/NRPS/AGS probados contra instancia externa |
| BB-39 | Integración | Registro oficial en CAMPUS Canarias | ⚪ | Credenciales/deployment proporcionados por administración CAMPUS |

## Orden de ejecución

### Fase A — Experiencia docente
1. BB-01 Centro global de actividad.
2. BB-04 Cola global de corrección.
3. BB-03 Detalle integral de calificación.
4. BB-10 Vista de progreso del grupo.

### Fase B — Experiencia del alumno
1. BB-20 Inicio orientado a próxima acción.
2. BB-08 Estados de progreso uniformes.
3. BB-09 Barra de progreso por RA.
4. BB-21 Calificaciones RA/CE.

### Fase C — Liberación adaptativa
1. BB-12 Fechas.
2. BB-13 Prerrequisitos por finalización.
3. BB-14 Prerrequisitos por puntuación.
4. BB-15 Secuencia obligatoria.
5. BB-16 Reglas por alumno/grupo.
6. BB-17/18/19 excepciones y exenciones.

### Fase D — Refinamiento
Búsqueda, recientes, accesibilidad, notificaciones, calendario, analítica y validación externa.

## Criterio de paso a verde

Una tarea solo cambia a 🟢 cuando:

1. existe implementación backend si procede;
2. existe interfaz utilizable;
3. existen permisos/aislamiento adecuados;
4. hay al menos una prueba automática del flujo crítico;
5. CI de `main` está verde;
6. la documentación refleja el comportamiento real.

## Principios que no se deben perder

- CAMPUS debe seguir siendo la puerta de entrada institucional.
- Las calificaciones definitivas vuelven a CAMPUS mediante AGS.
- La lógica FP se expresa por RA y CE.
- Los SCORM en uso son inmutables para intentos ya iniciados.
- Cada profesor conserva su propia API de IA.
- La IA propone o automatiza solo donde esté autorizado; no sustituye la trazabilidad ni la revisión docente.
- Los bancos privados de respuestas no se publican en Git.


## Avance ejecutado

### BB-01 / BB-04 — Centro de actividad docente

Implementado en la primera iteración:

- endpoint agregado `GET /api/dashboard/teacher`;
- métricas globales de grupos, alumnado, revisiones, recuperaciones, notas pendientes de CAMPUS y SCORM en curso;
- lista priorizada de trabajo que necesita atención;
- enlaces directos a la cola de revisión o al cuaderno del grupo-módulo;
- soporte de `?tab=reviews` y `?tab=gradebook` en la pantalla de evaluación;
- prueba automática de agregación y navegación;
- CI verde en el commit `8bd50d683f81b5a932ebfd5b6f6dd34c8ff61dc2`.


## Cierre de la fase Blackboard Ultra

La fase funcional interna queda cerrada con los siguientes bloques operativos y cubiertos por CI:

- centro global de actividad y cola transversal de revisión;
- cuaderno RA/CE y detalle integral de cada alumno;
- matriz de progreso docente;
- progreso del alumno por módulo, RA y actividad;
- índice lateral, reanudación y navegación anterior/siguiente;
- fechas de apertura/cierre;
- prerrequisitos por realización y puntuación;
- secuencias obligatorias de RA y de actividades;
- disponibilidad para todo el grupo o alumnado seleccionado;
- intentos extra y tiempo extra individual;
- exenciones sin penalización;
- búsqueda transversal y búsqueda contextual;
- favoritos y recientes;
- notificaciones y calendario;
- cronología de actividad;
- señales descriptivas de baja actividad, sin inferencias ni diagnósticos;
- interfaz responsive en las superficies principales;
- landmarks, salto a contenido y regiones `aria-live`;
- validación estática de JavaScript, IDs y selectores de los frontends principales en CI.

### Evidencia de validación

Las pruebas funcionales de aceptación Blackboard y secuenciación están incluidas en la batería general de CI. El commit `e2212a5b0ceff7b8788162cdeeea51565c833c55` finalizó correctamente e incluye:

- compilación Python;
- batería `pytest`;
- validación de scripts Ubuntu;
- validación de Docker Compose;
- validador JavaScript/HTML de frontends.

### Pendientes exclusivamente externos

- **BB-38** permanece ⚪: prueba contra una instancia Moodle/CAMPUS real. El repositorio dispone de pruebas locales LTI 1.3/Deep Linking/NRPS/AGS, pero una validación real necesita una plataforma externa y credenciales.
- **BB-39** permanece ⚪: alta oficial del LMS como herramienta externa en CAMPUS del Gobierno de Canarias. Depende de la administración de CAMPUS.


## Estado operativo y límites de garantía

Las tareas **BB-01 a BB-37** están implementadas en el repositorio y cubiertas por la batería de CI, directamente o mediante pruebas integrales de aceptación.

La accesibilidad marcada en BB-34 significa que las superficies principales disponen de una base técnica verificable —navegación mediante teclado, salto al contenido, foco, landmarks, regiones ARIA y controles asociados—. No se presenta como una certificación externa WCAG 2.2.

Las tareas **BB-38 y BB-39** no pueden pasar a verde desde el repositorio porque requieren infraestructura y autorización ajenas al LMS:

- una instancia Moodle/CAMPUS real con credenciales LTI;
- el alta institucional de la herramienta por la administración de CAMPUS.

Estas dependencias externas no impiden desplegar ni probar el LMS, pero sí impiden afirmar que la integración institucional con CAMPUS está certificada antes de realizar el piloto real.
