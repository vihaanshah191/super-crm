"""Ingestion orchestration: turns a SourceAdapter's parsed records into
RawObservations, runs entity resolution, and recomputes Evidence + canonical
Company fields for whatever changed.

This is the shared core used by both the Celery task layer
(app.ingestion.jobs.tasks) and the vertical-slice demo script -- job
scheduling and orchestration logic stay separate so the pipeline itself is
testable without Celery or a running worker.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.compliance.source_policy import SourcePolicy
from app.core.logging import get_logger
from app.ingestion.confidence.engine import ObservationForConfidence, compute_field_confidence
from app.ingestion.entity_resolution.matcher import IdentitySignals
from app.ingestion.entity_resolution.resolver import resolve
from app.models.company import Company, CompanyAlias
from app.models.evidence import Evidence, EvidenceObservation
from app.models.observation import RawObservation
from app.models.source import Source
from app.source_adapters.base import ObservationDraft, ParsedRecord, SourceAdapter

logger = get_logger(__name__)

# Fields whose normalized_value maps directly onto a Company column (after a
# type cast). Fields not listed here (e.g. "products", "employee_range_min")
# are handled by dedicated logic in _apply_field_to_company.
_DIRECT_STRING_FIELDS = {
    "legal_name",
    "website",
    "public_phone",
    "public_email",
    "registered_address",
    "company_type",
    "cin",
    "gstin",
}


@dataclass(frozen=True)
class IngestResult:
    company_id: uuid.UUID | None
    decision: str  # "auto_match" | "review" | "new_company" | "no_match"
    observation_ids: list[uuid.UUID]


def ingest_parsed_record(
    db: Session,
    adapter: SourceAdapter,
    source: Source,
    policy: SourcePolicy,
    record: ParsedRecord,
) -> IngestResult:
    """Validate, normalize, and store one parsed record as RawObservations,
    resolving it to a canonical Company (or flagging it for review)."""
    policy.assert_collection_allowed()

    if not adapter.validate(record):
        logger.warning(
            "record_validation_failed",
            extra={"extra_fields": {"source": source.name, "external_ref": record.external_ref}},
        )
        return IngestResult(company_id=None, decision="no_match", observation_ids=[])

    drafts = adapter.normalize(record)
    if not drafts:
        return IngestResult(company_id=None, decision="no_match", observation_ids=[])

    signals = _signals_from_drafts(drafts)
    incoming_payload = {d.field: d.normalized_value for d in drafts}

    # Persist observations first (unattached to a company) so entity
    # resolution always has a durable observation_id to attach review
    # candidates to, and so nothing is lost if resolution errors out.
    collected_at = datetime.now(timezone.utc)
    observations: list[RawObservation] = []
    for draft in drafts:
        obs = RawObservation(
            company_id=None,
            source_id=source.id,
            source_type=source.source_type,
            source_url=record.source_url,
            field=draft.field,
            raw_value=draft.raw_value,
            normalized_value=draft.normalized_value,
            collected_at=collected_at,
            source_published_at=record.source_published_at,
            confidence=draft.confidence,
            verification_type=draft.verification_type,
            collector_version=adapter.collector_version,
            metadata_json=draft.metadata,
        )
        db.add(obs)
        observations.append(obs)
    db.flush()  # assign ids

    outcome = resolve(db, observations[0].id, signals, incoming_payload)

    if outcome.decision == "auto_match":
        company = outcome.company
    elif outcome.decision == "review":
        return IngestResult(company_id=None, decision="review", observation_ids=[o.id for o in observations])
    else:
        # No existing company matched -- only create a new canonical company
        # if we have enough identity signal to seed one (a name at minimum).
        if not signals.normalized_name:
            return IngestResult(company_id=None, decision="no_match", observation_ids=[o.id for o in observations])
        company = _create_company_stub(db, signals, incoming_payload)
        outcome_decision = "new_company"
        for obs in observations:
            obs.company_id = company.id
        recompute_company_evidence(db, company.id)
        return IngestResult(company_id=company.id, decision=outcome_decision, observation_ids=[o.id for o in observations])

    for obs in observations:
        obs.company_id = company.id
    recompute_company_evidence(db, company.id)
    return IngestResult(company_id=company.id, decision=outcome.decision, observation_ids=[o.id for o in observations])


def confirm_match(db: Session, match_candidate_id: uuid.UUID, reviewed_by: str) -> Company:
    """Human-reviewer action: accept an ambiguous EntityMatchCandidate,
    attaching the observations it was raised for to the candidate company.

    Sibling observations are identified by (source_id, collected_at) --
    all ObservationDrafts produced from one ParsedRecord share the same
    collected_at timestamp, since they're written in one batch by
    ingest_parsed_record(). A production system with multiple records
    landing in the same instant per source should widen this to an explicit
    batch/record id instead of relying on timestamp equality.
    """
    from app.models.match_candidate import EntityMatchCandidate

    candidate = db.get(EntityMatchCandidate, match_candidate_id)
    if candidate is None:
        raise ValueError("match candidate not found")
    if candidate.status != "pending":
        raise ValueError(f"match candidate is not pending (status={candidate.status})")

    seed_observation = db.get(RawObservation, candidate.observation_id)
    sibling_observations = list(
        db.scalars(
            select(RawObservation).where(
                RawObservation.source_id == seed_observation.source_id,
                RawObservation.collected_at == seed_observation.collected_at,
                RawObservation.company_id.is_(None),
            )
        )
    )
    for obs in sibling_observations:
        obs.company_id = candidate.candidate_company_id

    candidate.status = "confirmed"
    candidate.resolved_at = datetime.now(timezone.utc)
    candidate.resolved_by = reviewed_by
    db.flush()

    recompute_company_evidence(db, candidate.candidate_company_id)
    db.flush()
    return db.get(Company, candidate.candidate_company_id)


def reject_match(db: Session, match_candidate_id: uuid.UUID, reviewed_by: str) -> None:
    """Human-reviewer action: reject an ambiguous EntityMatchCandidate. The
    underlying observations stay unattached (company_id remains NULL) so they
    can be reconsidered later -- e.g. once more corroborating observations
    arrive, or a new company row is created for them."""
    from app.models.match_candidate import EntityMatchCandidate

    candidate = db.get(EntityMatchCandidate, match_candidate_id)
    if candidate is None:
        raise ValueError("match candidate not found")
    candidate.status = "rejected"
    candidate.resolved_at = datetime.now(timezone.utc)
    candidate.resolved_by = reviewed_by
    db.flush()


def _signals_from_drafts(drafts: list[ObservationDraft]) -> IdentitySignals:
    by_field = {d.field: d.normalized_value for d in drafts}
    email = by_field.get("public_email")
    email_domain = email.split("@")[-1].lower() if email and "@" in email else None
    return IdentitySignals(
        cin=by_field.get("cin"),
        gstin=by_field.get("gstin"),
        website_domain=by_field.get("website"),
        normalized_name=by_field.get("canonical_name"),
        state=by_field.get("state"),
        postal_code=by_field.get("postal_code"),
        public_email_domain=email_domain,
    )


def _create_company_stub(db: Session, signals: IdentitySignals, incoming_payload: dict) -> Company:
    company = Company(
        canonical_name=incoming_payload.get("canonical_name") or signals.normalized_name,
        normalized_name=signals.normalized_name,
        cin=signals.cin,
        gstin=signals.gstin,
        website_domain=signals.website_domain,
        state=signals.state.title() if signals.state else None,
        postal_code=signals.postal_code,
        confidence=0.0,
        source_count=0,
    )
    db.add(company)
    db.flush()
    if signals.normalized_name:
        db.add(
            CompanyAlias(
                company_id=company.id,
                alias=incoming_payload.get("canonical_name") or signals.normalized_name,
                normalized_alias=signals.normalized_name,
            )
        )
    return company


def recompute_company_evidence(db: Session, company_id: uuid.UUID, fields: list[str] | None = None) -> None:
    """Recompute Evidence rows (and the affected Company columns) from all
    RawObservations currently attached to `company_id`. Safe to re-run at any
    time -- e.g. after a review-queue merge, or when reprocessing historical
    observations under updated confidence rules."""
    company = db.get(Company, company_id)
    if company is None:
        return

    field_query = select(RawObservation.field).where(RawObservation.company_id == company_id).distinct()
    target_fields = fields or [row for row in db.scalars(field_query)]

    now = datetime.now(timezone.utc)
    most_recent_verified: datetime | None = None
    confidences: list[float] = []

    for field_name in target_fields:
        obs_rows = list(
            db.scalars(
                select(RawObservation)
                .where(RawObservation.company_id == company_id, RawObservation.field == field_name)
                .join(Source, Source.id == RawObservation.source_id)
                # Deterministic ordering: without this, row order is whatever Postgres's
                # query planner happens to return (unspecified, can shift with table
                # size/vacuum state). compute_field_confidence() no longer relies on
                # input order to break value ties, but a stable order here still makes
                # the explanation's observation lists reproducible for debugging.
                .order_by(RawObservation.collected_at, RawObservation.id)
            )
        )
        if not obs_rows:
            continue

        source_ids = {o.source_id for o in obs_rows}
        sources = {s.id: s for s in db.scalars(select(Source).where(Source.id.in_(source_ids)))}

        scoring_input = [
            ObservationForConfidence(
                source_reliability_weight=sources[o.source_id].reliability_weight,
                verification_type=o.verification_type,
                normalized_value=o.normalized_value,
                collected_at=o.collected_at,
            )
            for o in obs_rows
        ]
        result = compute_field_confidence(scoring_input, now=now)
        confidences.append(result.confidence)

        if result.verification_type == "verified":
            newest = max(o.collected_at for o in obs_rows)
            most_recent_verified = max(most_recent_verified, newest) if most_recent_verified else newest

        existing_evidence = db.scalar(
            select(Evidence).where(Evidence.company_id == company_id, Evidence.field == field_name)
        )
        if existing_evidence is None:
            existing_evidence = Evidence(company_id=company_id, field=field_name)
            db.add(existing_evidence)

        existing_evidence.value = result.value
        existing_evidence.confidence = result.confidence
        existing_evidence.verification_type = result.verification_type
        existing_evidence.explanation = result.explanation
        existing_evidence.computed_at = now
        db.flush()

        # Replace the observation linkage for this evidence row.
        db.query(EvidenceObservation).filter(EvidenceObservation.evidence_id == existing_evidence.id).delete()
        for o in obs_rows:
            db.add(EvidenceObservation(evidence_id=existing_evidence.id, observation_id=o.id))

        _apply_field_to_company(company, field_name, result.value)

    company.source_count = len(
        {o.source_id for o in db.scalars(select(RawObservation).where(RawObservation.company_id == company_id))}
    )
    company.confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    if most_recent_verified:
        company.last_verified_at = most_recent_verified
    db.flush()


def _apply_field_to_company(company: Company, field_name: str, value: str | None) -> None:
    if value is None:
        return
    if field_name == "canonical_name":
        company.normalized_name = value
        return
    if field_name in _DIRECT_STRING_FIELDS:
        setattr(company, field_name, value)
        return
    if field_name == "industry":
        company.industry = value
        return
    if field_name == "state":
        company.state = value.title()
        return
    if field_name == "postal_code":
        company.postal_code = value
        return
    if field_name == "products":
        company.products = [p.strip() for p in value.split(",") if p.strip()]
        return
    if field_name == "employee_count":
        company.employee_count = _safe_int(value)
        return
    if field_name == "employee_range_min":
        company.employee_range_min = _safe_int(value)
        return
    if field_name == "employee_range_max":
        company.employee_range_max = _safe_int(value)
        return
    if field_name == "incorporation_date":
        company.incorporation_date = _safe_date(value)
        return
    if field_name in ("authorized_capital_inr", "paidup_capital_inr"):
        return  # tracked as Evidence only; not part of the canonical column set
    # Unknown fields are still preserved as Evidence (above); they simply
    # don't have a canonical Company column to project onto.


def _safe_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
