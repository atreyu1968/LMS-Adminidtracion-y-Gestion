# Espacio docente: grupos y biblioteca SCORM

## Objetivo

Cada profesor debe poder trabajar con autonomía dentro del LMS sin mezclar sus contenidos privados con los de otros docentes. El modelo diferencia:

- **grupos docentes**, que representan clases concretas;
- **módulos**, que representan contenidos reutilizables;
- **biblioteca SCORM**, que pertenece al profesor y puede reutilizarse en varios módulos y grupos.

## Grupos

Un grupo puede tener dos orígenes:

### Grupo local

Creado directamente por un profesor desde el panel docente.

Características:

- tiene un propietario;
- puede tener profesores colaboradores;
- puede incorporar alumnos manualmente;
- permite importación de alumnado mediante CSV;
- puede activar autoinscripción mediante un código;
- admite curso académico, etiqueta, descripción y configuración;
- puede asignar varios módulos;
- puede cerrarse sin borrar el historial.

### Grupo CAMPUS

Se crea automáticamente al recibir un contexto LTI de CAMPUS/Moodle.

Características:

- los profesores y alumnos se reconocen por su identidad LTI;
- la matrícula puede sincronizarse mediante NRPS;
- las calificaciones pueden devolverse mediante AGS;
- no se elimina desde el LMS, porque su ciclo de vida depende de CAMPUS;
- puede utilizar los mismos módulos y SCORM que un grupo local.

## Configuración del grupo

Actualmente se incluyen:

- `allow_self_enrol`: permite que un usuario autenticado se incorpore mediante código;
- `show_scores`: controla si las notas consolidadas se muestran al alumnado;
- `max_attempts_default`: número de intentos por defecto para los SCORM del grupo; `0` significa sin límite.

El objeto `settings_json` permite añadir posteriormente opciones de evaluación, recuperación, modo examen, calendario, disponibilidad o condiciones por RA sin modificar el esquema de la tabla.

## Roles del grupo

### Propietario
Puede editar el grupo, añadir alumnos, añadir profesores colaboradores, rotar el código y cerrar un grupo local.

### Profesor colaborador
Puede gestionar alumnado, configuración y contenidos del grupo, pero no puede retirar al propietario ni añadir otros profesores si no es propietario.

### Alumno
Puede acceder únicamente a los módulos asignados al grupo en el que está matriculado.

## Biblioteca SCORM

Cada paquete SCORM tiene:

- propietario;
- título y descripción;
- nombre del fichero original;
- hash SHA-256;
- estándar detectado;
- versión interna;
- fecha de carga;
- visibilidad;
- ruta de almacenamiento.

### Privado

Solo el propietario puede incorporarlo a nuevos módulos.

### Compartido

Otros profesores pueden reutilizarlo en un módulo propio. El ZIP físico no se duplica: se crea únicamente una relación entre el módulo y el paquete.

Un profesor que reutiliza un SCORM compartido no se convierte en propietario del fichero original.

## Reutilización

La relación es:

```text
Profesor
  └─ Biblioteca SCORM
       └─ Paquete SCORM
            ├─ Módulo A
            │    ├─ Grupo 1
            │    └─ Grupo 2
            └─ Módulo B
                 └─ Grupo 3
```

De esta manera un mismo SCORM puede utilizarse en varios grupos sin volver a subirlo.

## Compatibilidad SCORM

El runtime actual reconoce:

- SCORM 1.2 mediante `window.API`;
- SCORM 2004 mediante `window.API_1484_11`.

El backend detecta la versión desde `imsmanifest.xml`, conserva el CMI del intento y normaliza la información principal de progreso, puntuación, localización y `suspend_data`.

## Aislamiento entre profesores

Las reglas que se prueban automáticamente son:

1. un profesor no puede ver en su biblioteca privada el SCORM de otro;
2. no puede adjuntarlo a su módulo mientras siga siendo privado;
3. cuando el propietario lo marca como compartido, sí puede reutilizarlo;
4. un profesor no puede gestionar un grupo ajeno;
5. cuando el propietario lo incorpora como profesor colaborador, obtiene acceso docente;
6. un colaborador no puede añadir otros profesores al grupo salvo que sea propietario;
7. un alumno no puede descubrir módulos que no estén asignados a su contexto.

## Panel docente

El panel está disponible en:

```text
/teacher.html
```

La portada muestra el acceso al panel automáticamente cuando la sesión LTI tiene rol de profesor o administrador.

Desde este panel ya se puede:

- crear y gestionar grupos;
- importar alumnado CSV;
- añadir profesores colaboradores;
- crear módulos;
- subir SCORM a la biblioteca;
- cambiar un SCORM entre privado y compartido;
- asociar SCORM a módulos;
- asociar módulos a grupos;
- activar un grupo como contexto de trabajo;
- sincronizar un grupo CAMPUS mediante NRPS;
- configurar autoinscripción, notas e intentos;
- cerrar grupos locales.

## Evolución prevista

La siguiente fase del espacio docente incorporará el editor de estructura RA/CE, reglas de evaluación, calendario/disponibilidad, importación y exportación completa de módulos y el panel de seguimiento de resultados por alumno.
