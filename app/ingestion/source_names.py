"""Batched Source-name lookups for API responses that want to show which
source(s) actually back a company or an Evidence rollup -- e.g. search
results showing "MCA, Super CRM Demo Dataset" instead of only a bare
source_count, or an evidence row showing which source(s) support it.

Read-only projections over RawObservation/EvidenceObservation -- no new
state, no N+1 (one batched query per caller, keyed by the ids already being
returned in that response), consistent with source_health.py's "derive,
don't duplicate" posture.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence import EvidenceObservation
from app.models.observation import RawObservation
from app.models.source import Source


def _display(source_name: str, display_name: str | None) -> str:
    # display_name is what a UI shows a human (see Source's docstring);
    # name is the internal, stable identifier ("mca_company_master_data")
    # and is deliberately not user-facing. Callers rendering this list
    # directly (the Discover results table, an evidence row's Source
    # column) need the human-facing form, same as every other place the
    # frontend already renders a source (e.g. the Ingestion Status page).
    return display_name or source_name


def source_names_by_company(db: Session, company_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """company_id -> sorted, deduplicated Source display names for every
    source that has contributed at least one RawObservation to that
    company. A company absent from the returned dict has no observations
    at all (callers should treat that as an empty list, not an error)."""
    if not company_ids:
        return {}
    rows = db.execute(
        select(RawObservation.company_id, Source.name, Source.display_name)
        .join(Source, Source.id == RawObservation.source_id)
        .where(RawObservation.company_id.in_(company_ids))
        .distinct()
    ).all()
    grouped: dict[uuid.UUID, list[str]] = defaultdict(list)
    for company_id, name, display_name in rows:
        grouped[company_id].append(_display(name, display_name))
    return {cid: sorted(names) for cid, names in grouped.items()}


def source_names_by_evidence(db: Session, evidence_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """evidence_id -> sorted, deduplicated Source display names among the
    RawObservations backing that Evidence rollup (via EvidenceObservation)."""
    if not evidence_ids:
        return {}
    rows = db.execute(
        select(EvidenceObservation.evidence_id, Source.name, Source.display_name)
        .join(RawObservation, RawObservation.id == EvidenceObservation.observation_id)
        .join(Source, Source.id == RawObservation.source_id)
        .where(EvidenceObservation.evidence_id.in_(evidence_ids))
        .distinct()
    ).all()
    grouped: dict[uuid.UUID, list[str]] = defaultdict(list)
    for evidence_id, name, display_name in rows:
        grouped[evidence_id].append(_display(name, display_name))
    return {eid: sorted(names) for eid, names in grouped.items()}
