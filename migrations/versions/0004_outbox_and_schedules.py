"""Tables outbox et scheduled_tasks

Revision ID: 0004_outbox_and_schedules
Revises: 0003_email_addresses
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_outbox_and_schedules"
down_revision: str | None = "0003_email_addresses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- File de sortie des e-mails ---
    # C'est une file, pas un journal : une ligne envoyée est supprimée. Elle
    # contient une adresse et un corps de message, donc des données
    # personnelles qu'on ne conserve pas au-delà de l'envoi.
    op.create_table(
        "outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # La boucle d'envoi cherche les messages dus, triés par échéance.
    op.create_index("idx_outbox_due", "outbox", ["next_attempt_at"])

    # --- Tâches planifiées ---
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        # Le prompt rejoué chaque jour. Rédigé à la création, comme tout
        # message : c'est une instruction persistante, donc un vecteur
        # d'injection s'il n'était pas contrôlé.
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "email_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_addresses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Heure locale ET fuseau : une heure naïve se décalerait d'une heure
        # deux fois par an.
        sa.Column("run_at_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_schedules_user", "scheduled_tasks", ["user_id"])
    # Index partiel : la boucle ne cherche que parmi les tâches actives, donc
    # l'index ne porte que sur celles-là.
    op.create_index(
        "idx_schedules_due",
        "scheduled_tasks",
        ["next_run_at"],
        postgresql_where=sa.text("enabled"),
    )


def downgrade() -> None:
    op.drop_index("idx_schedules_due", table_name="scheduled_tasks")
    op.drop_index("idx_schedules_user", table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
    op.drop_index("idx_outbox_due", table_name="outbox")
    op.drop_table("outbox")