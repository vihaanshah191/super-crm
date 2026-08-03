from fastapi import FastAPI

from app.api.routes.companies import router as companies_router
from app.api.routes.search import router as search_router
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(title="Super CRM", version="0.1.0")
app.include_router(companies_router)
app.include_router(search_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
