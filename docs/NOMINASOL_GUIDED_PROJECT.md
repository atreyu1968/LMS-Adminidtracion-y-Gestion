# Proyecto guiado NOMINASOL 2026

## Finalidad

El módulo convierte el aprendizaje de TeamSystem NOMINASOL en una experiencia profesional tutorizada. El alumno no recibe una sucesión de instrucciones aisladas: asume la gestión laboral de **ATLÁNTICO GESTIÓN INTEGRAL, S.L.** y mantiene la empresa durante un ejercicio completo.

La prioridad no es acertar a la primera. La prioridad es que el alumno sea capaz de detectar una discrepancia, comprender su origen, corregirla y llegar a un estado profesionalmente coherente.

## Diseño didáctico

Cada hito comienza con una situación de empresa explicada en lenguaje natural. Después se indica qué se pretende conseguir y por qué esa operación tiene consecuencias en los meses posteriores. Solo entonces aparece el recorrido dentro de NOMINASOL.

Cada paso incluye una explicación de lo que se está haciendo y por qué; la ruta de menú que permite orientarse; una captura real de NOMINASOL cuando existe una imagen oficial disponible; qué debe observar el alumno antes de continuar; un error habitual explicado de forma preventiva; una lista de comprobación previa a la entrega; y una evidencia que demuestra el estado real de la empresa.

El SCORM no utiliza recreaciones de la interfaz. Las imágenes actualmente enlazadas proceden del Centro de Ayuda oficial de TeamSystem España. Cuando una pantalla todavía no tiene una captura incorporada, el sistema muestra la explicación y la ruta, pero no inventa una imagen.

## Recorrido anual

El proyecto contiene 17 hitos encadenados: primer contacto con NOMINASOL; empresa de entrenamiento; creación de la empresa definitiva; plantilla y contratos; enero y primer cierre; febrero y variables; marzo e incapacidad temporal; abril y primer trimestre; mayo y atrasos; junio y retribución en especie; julio y paga extraordinaria; agosto y sustitución; septiembre y finiquito; octubre y suspensión; noviembre e incidencia económica individualizada; diciembre y última paga extraordinaria; y auditoría final con apertura de 2027.

El alumno no puede iniciar un hito dependiente mientras el anterior no esté validado.

## Evidencias y corrección

Las evidencias se almacenan fuera del modelo de datos SCORM, en el almacenamiento del LMS, y se vinculan al registro SCORM y al hito concreto.

Los estados posibles son no iniciado, en proceso, evidencia enviada, pendiente de revisión, necesita corrección y superado.

Cada nuevo envío crea un intento adicional. Nunca se reemplaza la evidencia anterior. Esto permite comprobar la evolución del alumno y no solo el producto final.

## IA

Cada profesor utiliza su propia API de IA, ya soportada por el LMS.

Cuando la corrección automática está activada para el tipo evidence, una captura de pantalla puede ser enviada al modelo multimodal junto con el contexto del hito, el resultado esperado, la lista de comprobaciones, las observaciones del alumno y la rúbrica general del profesor.

La respuesta debe ser JSON estructurado con veredicto, confianza, comprobaciones y pista de corrección.

Un resultado con confianza suficiente puede superar automáticamente un hito si ese hito permite validación automática. Los hitos críticos, las evidencias no visuales y los resultados de baja confianza quedan para el profesor.

## Capturas reales

El archivo modules/nominasol2026/guided.json contiene las imágenes oficiales actualmente utilizadas. Entre ellas hay pantallas reales de ficha de trabajador, contratos, cálculo de nóminas, incapacidad temporal, paga extraordinaria, atrasos, modelo 111, modelo 190, copia de seguridad y apertura del siguiente ejercicio.

El directorio modules/nominasol2026/screenshots/ queda reservado para capturas propias de la versión educativa 2026 cuando se desee sustituir un enlace oficial por una copia local autorizada.

## Aprovisionamiento

El módulo se empaqueta desde modules/nominasol2026/scorm/master/ y se registra como módulo compartido del catálogo mediante:

    POST /api/admin/catalog/nominasol2026/provision

El proceso crea o actualiza el módulo y adjunta el SCORM maestro.

## Alumno

Al abrir el SCORM desde el LMS, scorm-bridge.js expone una pequeña API de proyecto guiado al SCO. El paquete puede recuperar el estado del proyecto, iniciar un hito, subir una evidencia, mostrar la devolución de IA o profesor y actualizar el porcentaje SCORM.

## Profesor

Desde el grupo, el profesor dispone de **Seguimiento de hitos**. Puede ver el porcentaje de avance de cada alumno, el número de hitos superados, las evidencias pendientes, el historial de intentos, el resumen de la revisión IA y el archivo original. También puede aceptar una evidencia o devolverla con un comentario concreto.

La validación docente permanece siempre disponible aunque la IA esté activa.


## Expediente de empresa y bandeja mensual

La versión 2026.2 incorpora `scenario.json`, que contiene la empresa ficticia completa, plantilla inicial, tablas salariales de referencia, política retributiva y la documentación que llega al departamento de Recursos Humanos durante el año.

El alumno ya no recibe únicamente una instrucción del tipo “calcula febrero”. Antes de entrar en NOMINASOL abre la bandeja de RRHH y encuentra comunicaciones internas, solicitudes de trabajadores, partes de IT, autorizaciones de variables, comunicaciones de contratación, avisos de vencimiento, órdenes de liquidación y documentación de cierre.

Algunos hitos tienen variantes asignadas de forma determinista por alumno. La misma persona conserva siempre la misma variante aunque cierre y vuelva a abrir el SCORM. Actualmente se individualizan, entre otros, la variable de febrero, las fechas de la IT de marzo y la incidencia económica de noviembre.

La IA recibe también el contexto de la variante y los documentos de ese alumno, evitando validar una captura únicamente porque se parece a la solución de otro compañero.

## Guía docente integrada

`teacher-guide.json` aporta al profesorado un recorrido paralelo. Desde el propio grupo puede abrir la Guía docente y consultar qué pretende cada hito, cuál es el estado que se espera encontrar, qué conviene revisar y cuáles requieren validación manual.

El objetivo es que el proyecto pueda impartirse aunque el docente todavía esté aprendiendo NOMINASOL.
