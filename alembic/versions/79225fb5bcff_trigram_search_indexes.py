"""trigram search indexes

Revision ID: 79225fb5bcff
Revises: 3253b1fbe58d
Create Date: 2026-08-03 15:37:21.832762

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79225fb5bcff'
down_revision: Union[str, Sequence[str], None] = '3253b1fbe58d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_companies_normalized_name_trgm "
        "ON companies USING gin (normalized_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_company_aliases_normalized_alias_trgm "
        "ON company_aliases USING gin (normalized_alias gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_companies_website_domain_trgm "
        "ON companies USING gin (website_domain gin_trgm_ops)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_companies_website_domain_trgm")
    op.execute("DROP INDEX IF EXISTS ix_company_aliases_normalized_alias_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_normalized_name_trgm")
