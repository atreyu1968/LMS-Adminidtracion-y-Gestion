# Integración con CAMPUS del Gobierno de Canarias

## Objetivo

Que el alumnado y el profesorado entren en este LMS desde CAMPUS sin una segunda autenticación y que las calificaciones evaluables puedan volver al calificador de CAMPUS.

CAMPUS está basado en Moodle. La integración recomendada es registrar este LMS como **herramienta externa LTI 1.3 Advantage**.

## Servicios que necesitamos

### LTI Core 1.3
Responsable de la autenticación federada y del lanzamiento seguro de la actividad.

### Names and Role Provisioning Services (NRPS)
Permite obtener la lista de participantes y sus roles en el contexto autorizado por CAMPUS.

Uso previsto:
- crear/actualizar matrículas;
- reconocer profesorado;
- detectar bajas;
- evitar mantener manualmente dos listas de alumnos.

### Assignment and Grade Services (AGS)
Permite enviar calificaciones desde el LMS al libro de calificaciones de CAMPUS.

Uso previsto:
- una columna por actividad, RA/UT o nota final, según configuración;
- devolución de puntuación y comentario;
- reenvío tras rectificación docente;
- auditoría de la última sincronización.

### Deep Linking
Permite que, al añadir una actividad en CAMPUS, el profesorado abra un selector del LMS y elija:
- un módulo completo;
- un RA/UT;
- un SCORM;
- una actividad evaluable;
- un examen.

## Datos de registro del Tool

La herramienta expone:

- Tool URL: `<BASE>/lti/launch`
- Initiate login URL: `<BASE>/lti/login`
- JWKS URL: `<BASE>/lti/jwks`
- Deep Linking URL: `<BASE>/lti/deep-link`

CAMPUS/Moodle debe proporcionar:

- Platform ID / issuer;
- Client ID;
- Authentication request URL;
- Access token URL;
- Public keyset / JWKS URL;
- Deployment ID.

Estos valores se guardan como registro de plataforma, nunca en código fuente.

## Permisos/servicios a solicitar

Para el piloto deben solicitarse, como mínimo:

- identificación y rol en el lanzamiento LTI;
- AGS score;
- AGS lineitem si se permite crear columnas desde el Tool;
- NRPS context membership readonly;
- Deep Linking para selección de contenido.

## Flujo del alumno

1. Entra en CAMPUS con el sistema habitual.
2. Abre la actividad del LMS.
3. CAMPUS inicia OIDC/LTI.
4. El LMS valida firma, issuer, audience, state, nonce y deployment.
5. El LMS vincula `issuer + sub` con su usuario interno.
6. Se abre directamente el recurso asignado.
7. El trabajo y las evidencias se guardan en el LMS.
8. Al consolidarse la calificación, AGS la devuelve al calificador de CAMPUS.

No se pedirá al alumno un token adicional.

## Flujo del profesor

1. Entra desde CAMPUS.
2. El rol LTI lo identifica como docente.
3. Puede gestionar únicamente cursos/módulos para los que tenga membresía docente.
4. Con Deep Linking selecciona qué contenido insertar.
5. El panel del LMS muestra evidencias y evaluación detallada.
6. CAMPUS conserva la calificación sincronizada para el flujo ordinario del centro.

## Condición administrativa

Aunque Moodle soporta LTI 1.3 Advantage, la posibilidad efectiva de registrar una herramienta externa en CAMPUS depende de la configuración y autorización del administrador de la plataforma del Gobierno de Canarias.

Por ello se prepararán dos niveles:

1. **Piloto Moodle controlado**, para validar técnicamente todo el flujo.
2. **Registro CAMPUS**, aportando a la administración las URLs, claves públicas, política de datos y alcance de permisos.

## Referencias técnicas

- https://www.gobiernodecanarias.org/educacion/web/formacion_profesional
- https://docs.moodle.org/501/en/mod/lti
- https://www.1edtech.org/standards/lti
- https://standards.1edtech.org/lti/guides/implementation_guide/implementation-guide
