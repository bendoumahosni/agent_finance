"""Table rate_limits

Revision ID: 0002_rate_limits
Revises: 0001_conversations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_rate_limits"
down_revision: str | None = "0001_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limits",
        # Clé primaire sur le seul user_id : une ligne par utilisateur,
        # réutilisée d'une fenêtre à l'autre. La table ne grossit donc pas
        # et aucune purge n'est nécessaire.
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")