# Arquitectura del LMS

## 1. Separación entre plataforma y contenido

El proyecto se divide en dos capas.

**Núcleo LMS**
- identidad y roles;
- organizaciones/centros;
- cursos;
- catálogo de módulos;
- matrículas;
- reproductor y estado SCORM;
- evidencias y evaluación;
- integración LTI;
- auditoría y exportación.

**Módulos didácticos**
- metadatos del módulo;
- RA/CE;
- teoría;
- SCORM;
- actividades;
- bancos de evaluación privados;
- rúbricas;
- configuración de evaluación.

GTH/0652 será el primer módulo migrado, pero ninguna tabla ni endpoint nuevo debe depender del literal `GRH0652`.

## 2. Modelo multi-profesor

La relación docente no se guarda como una única propiedad del curso. Un curso tiene múltiples `memberships`; cada una puede tener rol `teacher`, `student` o `admin`.

En modo CAMPUS, el rol se deriva del claim LTI `roles`. En modo administrativo se podrá completar o corregir mediante permisos locales auditados.

## 3. Modelo CAMPUS-first

CAMPUS se considera el **Platform** LTI y este proyecto el **Tool**.

El identificador local del alumno no se obtiene de parámetros manipulables de URL. Se crea una identidad federada mediante el par:

```text
(issuer de CAMPUS, subject LTI)
```

El contexto de un aula se identifica por:

```text
(issuer de CAMPUS, context.id)
```

Esto permite que el mismo usuario participe en distintos cursos y evita colisiones entre plataformas.

## 4. LTI 1.3 Advantage

Se implementan por fases:

1. OIDC login initiation.
2. Resource Link launch.
3. JWKS del Tool.
4. Validación de issuer, audience, nonce, state y deployment.
5. AGS para notas.
6. NRPS para matrículas/roles.
7. Deep Linking para selección de módulos/RA/actividades.
8. Dynamic Registration, si CAMPUS lo admite y la administración lo autoriza.

## 5. SCORM

La primera compatibilidad será SCORM 1.2 porque GTH ya usa esa especificación.

La migración se hará en dos pasos:

1. ejecutar los SCO actuales sin modificar su contenido pedagógico;
2. sustituir gradualmente la API específica de GTH por una API SCORM genérica persistida en PostgreSQL.

Para paquetes de terceros se añadirá:
- subida ZIP;
- validación `imsmanifest.xml`;
- extracción segura;
- hash SHA-256;
- versionado;
- registro por alumno;
- persistencia de CMI;
- control de intentos;
- grade mapping hacia el motor de evaluación.

## 6. Seguridad de contenido SCORM

El objetivo de producción es servir los paquetes SCORM desde un origen de contenido separado y usar un bridge controlado para persistencia. Esto reduce el impacto de JavaScript incluido dentro de paquetes importados.

No se aceptarán rutas ZIP con traversal, ejecutables arbitrarios ni secretos dentro del paquete.

## 7. Persistencia

Producción: PostgreSQL.

Los bancos privados, claves LTI, claves de API de IA, copias de seguridad y ficheros con datos personales no se versionan.

## 8. Compatibilidad con GTH

Durante la migración, `CFGSAF/grh0652` permanece como referencia. Cada función migrada deberá tener prueba de regresión contra el comportamiento existente antes de considerar la nueva implementación equivalente.
