"""Table conversations

Revision ID: 0001_conversations
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_conversations"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        # UUID et non un entier auto-incrémenté : cet identifiant est visible
        # du client. Des entiers seraient énumérables et révéleraient le volume.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Le "sub" du JWT. Pas de clé étrangère vers une table users : la
        # source de vérité des identités est le fournisseur OIDC, pas la base.
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        # TIMESTAMPTZ et non TIMESTAMP : le serveur est en UTC, les
        # utilisateurs ailleurs. Un horodatage sans fuseau est ambigu.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Index taillé pour la requête de GET /conversations : filtrer sur un
    # utilisateur, trier par date décroissante.
    op.create_index(
        "idx_conversations_user",
        "conversations",
        ["user_id", sa.text("updated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_conversations_user", table_name="conversations")
    op.drop_table("conversations")
