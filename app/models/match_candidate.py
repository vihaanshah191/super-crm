import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class EntityMatchCandidate(UUIDPKMixin, TimestampMixin, Base):
    """An ambiguous entity-resolution decision awaiting human review.

    Created whenever an incoming observation's normalized identity signals
    score above the 'possible match' threshold but below the 'auto-match'
    threshold against an existing company -- never auto-merged.
    """

    __tablename__ = "entity_match_candidates"

    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_observations.id", ondelete="CASCADE"), index=True
    )
    candidate_company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )

    incoming_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    match_score: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    matched_signals: Mapped[dict] = mapped_column(JSONB, default=dict)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(255))
