from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import config, storage
from .auth import router as auth_router
from .chat import router as chat_router
from .documents import router as documents_router

app = FastAPI(title="Chat with PDF API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def check_origin(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("origin") != config.FRONTEND_ORIGIN:
        return Response(status_code=403)
    return await call_next(request)


@app.get("/config")
def public_config():
    return {"google_configured": bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET), "storage_ready": storage.ready()}


app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(chat_router)
