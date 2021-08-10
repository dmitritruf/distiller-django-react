from fastapi import APIRouter

from app.api.api_v1.endpoints import auth, files, notifications, scans

api_router = APIRouter()

api_router.include_router(files.router, prefix="/files", tags=["files"])
api_router.include_router(scans.router, prefix="/scans", tags=["scans"])
api_router.include_router(auth.router, prefix="", tags=["auth"])
api_router.include_router(notifications.router, prefix="", tags=["notfications"])
