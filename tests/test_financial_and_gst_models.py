"""Schema-level tests for company_gst_registrations and company_financials --
the multi-GSTIN and financial-year-history models proposed in the hardening
review. No adapter/pipeline wiring exists yet (that's future work); these
tests only verify the tables, constraints, and relationships behave as
designed.
"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.company import Company
from app.models.financials import CompanyFinancials
from app.models.gst_registration import CompanyGSTRegistration


def _company(**overrides) -> Company:
    defaults = dict(canonical_name="ABC Industries", normalized_name="abc industries")
    defaults.update(overrides)
    return Company(**defaults)


class TestCompanyGSTRegistration:
    def test_a_company_can_have_multiple_gst_registrations(self, db):
        company = _company()
        db.add(company)
        db.flush()

        db.add_all(
            [
                CompanyGSTRegistration(
                    company_id=company.id,
                    gstin="27ABCDE1234F1Z5",
                    registered_state="Maharashtra",
                    is_primary=True,
                ),
                CompanyGSTRegistration(
                    company_id=company.id,
                    gstin="24ABCDE1234F1Z6",
                    registered_state="Gujarat",
                    is_primary=False,
                ),
            ]
        )
        db.commit()

        registrations = (
            db.query(CompanyGSTRegistration)
            .filter(CompanyGSTRegistration.company_id == company.id)
            .all()
        )
        assert {r.gstin for r in registrations} == {"27ABCDE1234F1Z5", "24ABCDE1234F1Z6"}

    def test_gstin_is_globally_unique(self, db):
        company_a = _company()
        company_b = _company(canonical_name="XYZ Corp", normalized_name="xyz corp")
        db.add_all([company_a, company_b])
        db.flush()

        db.add(CompanyGSTRegistration(company_id=company_a.id, gstin="27ABCDE1234F1Z5", is_primary=True))
        db.commit()

        db.add(CompanyGSTRegistration(company_id=company_b.id, gstin="27ABCDE1234F1Z5", is_primary=True))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_registration_and_cancellation_dates(self, db):
        company = _company()
        db.add(company)
        db.flush()

        db.add(
            CompanyGSTRegistration(
                company_id=company.id,
                gstin="27ABCDE1234F1Z5",
                registered_state="Maharashtra",
                registration_date=date(2018, 7, 1),
                cancellation_date=date(2023, 3, 31),
                is_primary=True,
            )
        )
        db.commit()

        reg = db.query(CompanyGSTRegistration).filter_by(gstin="27ABCDE1234F1Z5").one()
        assert reg.registration_date == date(2018, 7, 1)
        assert reg.cancellation_date == date(2023, 3, 31)

    def test_deleting_company_cascades_to_registrations(self, db):
        company = _company()
        db.add(company)
        db.flush()
        db.add(CompanyGSTRegistration(company_id=company.id, gstin="27ABCDE1234F1Z5", is_primary=True))
        db.commit()

        db.delete(company)
        db.commit()

        assert db.query(CompanyGSTRegistration).filter_by(gstin="27ABCDE1234F1Z5").first() is None


class TestCompanyFinancials:
    def test_retains_separate_rows_per_financial_year(self, db):
        company = _company()
        db.add(company)
        db.flush()

        db.add_all(
            [
                CompanyFinancials(
                    company_id=company.id,
                    financial_year="FY2024",
                    annual_revenue_inr=100_000_000,
                    verification_type="verified",
                ),
                CompanyFinancials(
                    company_id=company.id,
                    financial_year="FY2025",
                    annual_revenue_inr=150_000_000,
                    verification_type="verified",
                ),
                CompanyFinancials(
                    company_id=company.id,
                    financial_year="FY2026",
                    annual_revenue_inr=200_000_000,
                    verification_type="estimated",
                ),
            ]
        )
        db.commit()

        rows = (
            db.query(CompanyFinancials)
            .filter(CompanyFinancials.company_id == company.id)
            .order_by(CompanyFinancials.financial_year)
            .all()
        )
        assert [r.financial_year for r in rows] == ["FY2024", "FY2025", "FY2026"]
        assert [float(r.annual_revenue_inr) for r in rows] == [100_000_000, 150_000_000, 200_000_000]
        # A later financial_year row does not overwrite or remove an earlier one --
        # this is the property the model exists to guarantee.

    def test_same_company_and_year_is_unique(self, db):
        company = _company()
        db.add(company)
        db.flush()

        db.add(CompanyFinancials(company_id=company.id, financial_year="FY2024", annual_revenue_inr=100_000_000))
        db.commit()

        db.add(CompanyFinancials(company_id=company.id, financial_year="FY2024", annual_revenue_inr=999_000_000))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_deleting_company_cascades_to_financials(self, db):
        company = _company()
        db.add(company)
        db.flush()
        db.add(CompanyFinancials(company_id=company.id, financial_year="FY2024", annual_revenue_inr=100_000_000))
        db.commit()

        db.delete(company)
        db.commit()

        assert (
            db.query(CompanyFinancials)
            .filter(CompanyFinancials.company_id == company.id)
            .first()
            is None
        )
