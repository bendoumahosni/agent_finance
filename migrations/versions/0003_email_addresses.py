"""Table email_addresses

Revision ID: 0003_email_addresses
Revises: 0002_rate_limits
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_email_addresses"
down_revision: str | None = "0002_rate_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        # Stockée en minuscules : la casse ne distingue pas deux adresses.
        sa.Column("address", sa.Text(), nullable=False),
        # NULL tant que l'utilisateur n'a pas cliqué le lien de confirmation.
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        # On stocke le HACHAGE du jeton, jamais le jeton : une copie de la
        # base ne doit pas permettre de confirmer une adresse.
        sa.Column("token_hash", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Une même adresse ne peut être enregistrée qu'une fois par
        # utilisateur : la contrainte est en base, pas dans le code.
        sa.UniqueConstraint("user_id", "address", name="uq_email_user_address"),
    )
    op.create_index("idx_email_user", "email_addresses", ["user_id"])
    # La confirmation cherche par hachage de jeton : sans index, elle
    # parcourrait toute la table.
    op.create_index("idx_email_token", "email_addresses", ["token_hash"])


def downgrade() -> None:
    op.drop_index("idx_email_token", table_name="email_addresses")
    op.drop_index("idx_email_user", table_name="email_addresses")
    op.drop_table("email_addresses")