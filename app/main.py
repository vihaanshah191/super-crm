from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.companies import router as companies_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.review_queue import router as review_queue_router
from app.api.routes.search import router as search_router
from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(title="Super CRM", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(companies_router)
app.include_router(search_router)
app.include_router(ingestion_router)
app.include_router(review_queue_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
