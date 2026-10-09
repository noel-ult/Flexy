"""A private, serialised single-laptop worker lease."""

import sqlalchemy as sa
from alembic import op

revision = "0002_remote_worker"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_worker",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column("job_id", sa.String(64)),
        sa.Column("lease_hash", sa.String(64)),
        sa.Column("lease_expires", sa.DateTime(timezone=True)),
    )
    op.execute(sa.text("INSERT INTO remote_worker (id, revision) VALUES (1, 0)"))


def downgrade() -> None:
    op.drop_table("remote_worker")
