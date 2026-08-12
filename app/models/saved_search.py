import uuid

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class SavedSearch(UUIDPKMixin, TimestampMixin, Base):
    """A named, reusable filter -- Phase 6 of the multi-source expansion
    (docs/multi_source_architecture.md).

    `filter_definition` stores a FilterCondition/FilterGroup (app.search.
    filter_types) as JSONB -- exactly the tree POST /api/search/companies/
    advanced already accepts, so a saved search executes through the same
    filter_compiler/advanced_query code every live search does (see
    app.api.routes.saved_searches.execute_saved_search); nothing here
    reimplements filter evaluation.

    `country_scope`/`source_scope` are crisp, non-evidence-backed
    narrowing (an ISO country-code list / a Source id list) applied
    alongside `filter_definition`, not folded into it -- see
    app.search.advanced_query._scope_clauses(). Empty means unscoped
    (all countries / all sources), never inferred.

    `created_by` is free text, matching the no-auth-system convention
    already used by EntityMatchCandidate.resolved_by /
    ReviewDecisionIn.reviewed_by elsewhere in this codebase -- there is no
    User model to foreign-key against.

    `selected_fields` is a display preference (which CompanyOut fields a
    UI should show for this saved search) -- validated against a known
    field list at creation time but not enforced by any server-side
    projection; the API still returns full CompanyOut rows.
    """

    __tablename__ = "saved_searches"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)

    country_scope: Mapped[list[str]] = mapped_column(ARRAY(String(2)), nullable=False, default=list)
    source_scope: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)

    filter_definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sort: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    selected_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (Index("ix_saved_searches_created_by", "created_by"),)
