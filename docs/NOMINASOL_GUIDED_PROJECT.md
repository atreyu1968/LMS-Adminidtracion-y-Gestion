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


## Solucionario técnico oculto

La versión 2026.3 incorpora `audit-rules.json`. Este archivo no forma parte del contenido visible del alumno y actúa como referencia técnica para la revisión con IA y para la guía docente.

Las reglas describen, hito por hito, qué estado debe poder observarse en la empresa, qué datos son críticos, qué errores impiden continuar y qué comprobaciones cambian según la variante individual del alumno.

La IA recibe estas reglas como contexto privado. Se le ordena expresamente utilizarlas para decidir si una evidencia es compatible con el estado esperado, pero no revelar al alumno la cifra o el dato exacto que todavía no haya conseguido. La devolución debe orientar hacia el campo, proceso o pantalla que necesita revisión.

Los hitos críticos marcados como `manual_validation` no pueden completarse automáticamente aunque la IA devuelva una confianza alta. En esos casos la evidencia pasa a revisión docente.

Actualmente requieren validación docente obligatoria, entre otros, la configuración de la empresa, la plantilla inicial, la actualización de tablas y atrasos, vacaciones y sustitución, y la auditoría final del ejercicio.

La Guía docente muestra el solucionario técnico únicamente al profesorado: operador de comprobación, valor esperado, gravedad y reglas específicas de cada variante.


## Ayuda diagnóstica y glosario

La versión 2026.4 añade `support.json`, una capa de ayuda visible diseñada específicamente para alumnado y profesorado que empiezan NOMINASOL desde cero.

Cada hito dispone de problemas frecuentes formulados como situaciones reconocibles: “el trabajador no aparece al calcular”, “la IT no cambia la nómina”, “los atrasos salen a cero”, “la paga extra no aparece” o “no puedo volver a 2026 después de abrir 2027”. La ayuda no entrega la solución: propone una secuencia de comprobaciones y obliga a volver al dato de origen.

Además existe una ayuda común para errores de navegación, empresa/ejercicio equivocado, restauración desde copias y diferencias entre variantes individuales.

El SCORM incluye también un glosario de consulta inmediata con conceptos de trabajo como devengos, bases de cotización, grupo de cotización, regularización de IRPF, IT, atrasos, retribución en especie, CRA, SILTRA y modelos 111/190. Está pensado como apoyo en contexto, no como una lista para memorizar.

Las capturas reales pueden ampliarse a pantalla grande con un clic. El objetivo es que el estudiante pueda mantener el SCORM abierto junto a NOMINASOL y comparar visualmente ambas pantallas sin perder legibilidad.


## Portafolio de evolución del alumno

La versión 2026.5 convierte el historial de evidencias en un portafolio visible para el propio alumno.

Desde cualquier momento del proyecto puede abrir **Ver mi evolución** y consultar, hito por hito, el estado alcanzado, número de intentos, archivos entregados, devoluciones de la IA, pistas recibidas y comentarios del profesor. Las evidencias muestran también una huella SHA-256 abreviada para ayudar a identificar de forma inequívoca qué archivo formó parte de cada intento.

El portafolio no oculta los errores previos. Si una evidencia fue devuelta y posteriormente corregida, aparecen ambas. Esto refuerza el objetivo del proyecto: valorar que el alumno llega correctamente al resultado y documentar cómo lo consigue.

El alumno puede preparar un informe imprimible desde el navegador y guardarlo como PDF mediante la función de impresión del propio sistema. También puede guardar un registro JSON con progreso y metadatos de las evidencias. La copia final restaurable de NOMINASOL continúa siendo una evidencia independiente e imprescindible del cierre anual.


## Versionado inmutable del proyecto guiado

La versión 2026.6 corrige un aspecto esencial para cursos ya iniciados. El LMS ya fijaba los intentos SCORM a la revisión con la que el alumno había empezado, pero la configuración narrativa del proyecto se cargaba desde los archivos actuales del repositorio.

A partir de esta versión, al aprovisionar una revisión se crea una **instantánea privada** de `guided.json`, `scenario.json`, `support.json`, `teacher-guide.json` y `audit-rules.json`. Esa instantánea queda vinculada al paquete SCORM y no se sirve como contenido web público.

El ZIP SCORM incorpora únicamente un marcador `guided-version.json` con la versión y las huellas de los archivos de configuración. De esta forma, cualquier modificación del caso, de la auditoría o de las ayudas produce una revisión nueva incluso aunque la interfaz SCORM no haya cambiado.

Las revisiones sucesivas quedan enlazadas mediante `lineage_root_id`, `supersedes_id` y `revision_number`. La asociación del módulo apunta solo a la revisión actual para nuevos alumnos, mientras que un alumno con un registro previo continúa fijado a su revisión anterior y conserva también la configuración guiada de aquella revisión.

Las reglas de auditoría permanecen en almacenamiento privado; el marcador público contiene únicamente hashes, nunca el solucionario.


## Precisión visual de las pantallas

La versión 2026.7 sustituye varias capturas genéricas por pantallas oficiales específicas de la operación que el alumno está realizando.

Se han incorporado, entre otras, capturas reales de la pantalla de acceso de NOMINASOL 2026, Situación del trabajador, Conceptos retributivos, Retribuciones especiales, Kilometraje, Categorías de convenio, Regularización de IRPF, Retribución en especie, Vacaciones y Descanso por nacimiento/cuidado del menor.

Noviembre dispone además de imagen dinámica por variante: el alumno con anticipo visualiza la ficha de anticipo, quien recibe bonus ve la configuración de bonus y quien trabaja el embargo simulado ve la pantalla de Embargos.

La fuente concreta del artículo oficial de TeamSystem se muestra debajo de cada captura cuando está disponible. Esto facilita mantener el material si TeamSystem modifica la interfaz o publica documentación más reciente.

También se ha ajustado el procedimiento de nacimiento y cuidado del menor a la operativa documentada por TeamSystem en 2026: dentro de NOMINASOL se registra mediante un parte de incapacidad temporal con la contingencia de descanso por maternidad/paternidad, sin confundirlo con una extinción de la relación laboral.


## Expediente documental operativo

La versión 2026.8 convierte la bandeja de RRHH en un expediente de trabajo utilizable fuera de la propia pantalla del SCORM.

Cada comunicación puede abrirse como documento independiente y dispone de acciones para **imprimir / guardar como PDF** o conservar una copia HTML. La versión imprimible incluye el tipo de documento, origen, cuerpo, campos de trabajo, variante individual cuando exista y un aviso visible de que se trata de una simulación educativa.

El alumno puede guardar además un **expediente anual completo** desde el SCORM. Ese expediente reúne todos los documentos que le han sido asignados, incluidas sus variantes personales.

El profesor dispone en Seguimiento de hitos de un botón **Expediente ZIP** para cada alumno. El ZIP se genera en el servidor a partir de la revisión exacta que ese alumno tiene fijada y contiene:

- índice general;
- datos públicos del supuesto y de la empresa;
- un HTML independiente por cada documento, organizado por hito;
- identificación de las variantes asignadas;
- un archivo LEEME con las limitaciones de uso educativo.

El ZIP no contiene reglas de auditoría, claves de corrección ni el solucionario técnico oculto. Así puede entregarse al alumno sin exponer la lógica privada de evaluación.
