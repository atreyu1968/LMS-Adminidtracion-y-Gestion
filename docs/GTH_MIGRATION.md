# Migración de GTH / 0652 al nuevo LMS

## Fuente fijada

La migración pública de **Gestión de Recursos Humanos (0652)** toma como referencia inmutable:

- repositorio: `atreyu1968/CFGSAF`
- commit: `6eaa240ce13a69e328c5b8d11ae2bdca99be7d17`
- ruta: `grh0652`

No se importa desde `main`; el commit queda fijado para que una modificación posterior del proyecto original no cambie silenciosamente el contenido migrado.

## Contenido público ya migrado

El catálogo nativo contiene:

- 4 resultados de aprendizaje;
- 33 criterios de evaluación;
- 198 actividades públicas de Portafolio;
- reglas base de evaluación;
- metadatos de trazabilidad;
- referencias a los cuatro paquetes SCORM públicos.

Los cuatro paquetes oficiales se aprovisionan desde el commit fijado:

- RA1 / UT1;
- RA2 / UT2;
- RA3 / UT3;
- RA4 / UT4.

El aprovisionamiento no necesita copiar el servidor SQLite antiguo.

## Reglas de evaluación conservadas

La configuración base preserva la escala interna de la aplicación estable:

- práctica: 3 intentos;
- Portafolio: 2 intentos;
- examen: 1 intento;
- recuperación: 1 intento;
- Portafolio: 40 %;
- examen: 60 %;
- umbral de RA: 50/100;
- umbral de CE: 50/100;
- mínimo de CE superados: 80 %;
- examen seguro con registro de incidentes;
- examen deshabilitado hasta que el banco privado esté correctamente cargado.

La conversión a la escala visible en CAMPUS se realiza al devolver la calificación; el motor interno trabaja en 0–100.

## Bancos privados

Las respuestas correctas, bancos de examen y recuperación **no forman parte del repositorio**.

Se cargan mediante:

```text
POST /api/evaluation/admin/modules/{module_id}/private-bank-bundle
```

o, para módulos propios del profesor:

```text
POST /api/evaluation/modules/{module_id}/private-bank-bundle
```

El paquete puede ser un JSON o un ZIP de JSON. El servidor valida la correspondencia con RA/CE y comprueba que las claves de Portafolio continúen correspondiendo al banco público mediante hash.

### Cobertura mínima configurada

Con la configuración base de GTH:

- examen: mínimo 3 preguntas por CE;
- recuperación: mínimo 2 actividades por CE.

El semáforo de preparación bloquea la consideración de “listo para evaluación” mientras falten claves o cobertura.

## IA

La IA no pertenece a GTH ni al centro: pertenece a cada profesor.

Cada docente configura:

- proveedor;
- URL;
- modelo;
- clave API cifrada;
- umbral de confianza;
- tipos de actividad;
- rúbrica general.

La IA solo actúa cuando ese profesor la vincula al grupo-módulo correspondiente.

Las actividades objetivas se corrigen de forma determinista. La IA se reserva para tipos semánticos, documentos o cálculos que no puedan resolverse de forma inequívoca por reglas locales.

No se envían a la API de IA:

- nombre;
- correo;
- identificador interno del alumno;
- identidad LTI.

Si no hay IA configurada, si falla la consulta o si la confianza queda por debajo del umbral, la respuesta permanece en la cola de revisión docente.

## Motor de evaluación

El nuevo LMS ya dispone de:

- intentos versionados;
- Portafolio;
- examen seguro;
- borrador de examen;
- temporizador;
- registro de incidencias;
- corrección determinista;
- propuesta IA;
- revisión docente;
- recálculo por CE;
- cálculo 40/60;
- recuperación selectiva por CE;
- cuaderno de grupo;
- semáforo de preparación;
- carga de bancos privados;
- retorno AGS de notas definitivas por RA.

## Integración CAMPUS

Deep Linking puede insertar:

- el módulo completo;
- un RA concreto.

Un RA insertado crea una columna calificable de 0–100. Cuando existe una calificación definitiva, el profesor puede enviarla mediante AGS.

No se envían a CAMPUS resultados provisionales, Portafolio incompleto, examen pendiente o revisiones humanas sin resolver.

## Aprovisionamiento

Tras desplegar el LMS:

```bash
curl -X POST \
  -H "X-Admin-Token: $LMS_ADMIN_TOKEN" \
  "$LMS_PUBLIC_BASE_URL/api/admin/catalog/gth0652/provision"
```

Esto:

1. importa RA, CE y Portafolio público;
2. descarga el commit fijado de CFGSAF;
3. construye los cuatro ZIP SCORM;
4. valida sus manifiestos;
5. los guarda como paquetes oficiales;
6. los asocia al módulo GTH.

Después se carga el banco privado desde un almacenamiento seguro.

## Estado de la migración

### Migrado

- estructura RA/CE;
- Portafolio público;
- cuatro SCORM públicos;
- configuración base;
- motor de evaluación;
- examen;
- recuperación;
- IA por profesor;
- panel docente de evaluación;
- Deep Linking por RA;
- NRPS;
- AGS por RA.

### Requiere operación externa

- cargar los bancos privados reales desde la fuente segura;
- validar el ciclo completo contra una instancia Moodle real;
- registrar la herramienta en CAMPUS del Gobierno de Canarias;
- piloto con alumnado real antes de retirar la aplicación GTH antigua.
