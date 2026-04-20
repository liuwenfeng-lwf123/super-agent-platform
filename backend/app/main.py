from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api.chat import router
from app.api.local import local_router, local_ws_router

app = FastAPI(title=settings.app_name, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(local_router)
app.include_router(local_ws_router)


@app.on_event("startup")
async def startup():
    import os
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.memory_dir, exist_ok=True)
    os.makedirs(settings.threads_dir, exist_ok=True)
