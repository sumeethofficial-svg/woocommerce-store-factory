from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, stores
from app.config import get_settings
from app.database import Base, engine, migrate
from app.logging_config import configure_logging
from app.services import executor, provisioner

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings)
    Base.metadata.create_all(engine)
    migrate()
    executor.start(settings.worker_threads)
    provisioner.resume_pending()
    yield
    executor.shutdown()


app = FastAPI(
    title=settings.app_name,
    description="Provision and manage WooCommerce stores on Kubernetes",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {key: value for key, value in error.items() if key in ("loc", "msg", "type")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


app.include_router(auth.router)
app.include_router(stores.router)


@app.get("/")
def root():
    return {"name": settings.app_name, "status": "running"}


@app.get("/health")
def health():
    return {"status": "healthy"}
