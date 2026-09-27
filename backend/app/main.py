from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router as api_router
from .ai import router as ai_router
from .catalog import router as catalog_router
from .dashboard import router as dashboard_router
from .evaluation import router as evaluation_router
from .experience import router as experience_router
from .db import init_db
from .lti import router as lti_router
from .learning import router as learning_router
from .integrations import router as integration_router
from .groups import router as groups_router
from .guided import router as guided_router
from .media import router as media_router
from .scorm import router as scorm_router
from .scorm_editor import router as scorm_editor_router
from .lti_keys import ensure_private_key
from .settings import get_settings


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_private_key()
    yield


app = FastAPI(
    title="LMS Administración y Gestión",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ai_router)
app.include_router(catalog_router)
app.include_router(dashboard_router)
app.include_router(evaluation_router)
app.include_router(experience_router)
app.include_router(lti_router)
app.include_router(learning_router)
app.include_router(integration_router)
app.include_router(groups_router)
app.include_router(guided_router)
app.include_router(media_router)
app.include_router(scorm_router)
app.include_router(scorm_editor_router)
