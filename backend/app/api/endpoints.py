from fastapi import APIRouter

from app.api.routers.admin import router as admin_router
from app.api.routers.auth import router as auth_router
from app.api.routers.health import router as health_router
from app.api.routers.schedules import router as schedules_router
from app.api.routers.students import router as students_router
from app.api.routers.teachers import router as teachers_router
from app.api.routers.training_plans import router as training_plans_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(health_router)
api_v1_router.include_router(schedules_router)
api_v1_router.include_router(admin_router)
api_v1_router.include_router(students_router)
api_v1_router.include_router(teachers_router)
api_v1_router.include_router(training_plans_router)
