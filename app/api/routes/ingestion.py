from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import IngestionJobOut, SourceOut
from app.db.base import get_db
from app.models.ingestion_job import IngestionJob
from app.models.source import Source

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)) -> list[SourceOut]:
    sources = list(db.scalars(select(Source).order_by(Source.name)))
    return [SourceOut.model_validate(s) for s in sources]


@router.get("/jobs", response_model=list[IngestionJobOut])
def list_ingestion_jobs(
    status: str | None = Query(default=None, description="Filter by job status (pending/running/success/failed/partial)"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[IngestionJobOut]:
    stmt = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(IngestionJob.status == status)
    jobs = list(db.scalars(stmt))
    return [IngestionJobOut.model_validate(j) for j in jobs]
