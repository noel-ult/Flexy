"""Initial Flexy job, log, and scoped-download-token tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("capability_hash", sa.String(length=64), nullable=False),
        sa.Column("target_os", sa.String(length=32), nullable=False),
        sa.Column("target_arch", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("upload_key", sa.String(length=512), nullable=False),
        sa.Column("upload_sha256", sa.String(length=64), nullable=False),
        sa.Column("upload_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("recipe_id", sa.String(length=160), nullable=True),
        sa.Column("analysis_json", sa.JSON(), nullable=True),
        sa.Column("blockers_json", sa.JSON(), nullable=True),
        sa.Column("verification_json", sa.JSON(), nullable=True),
        sa.Column("package_key", sa.String(length=512), nullable=True),
        sa.Column("report_key", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("build_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("upload_key"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_expires_at", "jobs", ["expires_at"])
    op.create_table(
        "job_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_logs_job_sequence", "job_logs", ["job_id", "sequence"], unique=True)
    op.create_table(
        "download_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_download_tokens_digest", "download_tokens", ["token_hash"], unique=True)
    op.create_index("ix_download_tokens_expires_at", "download_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_download_tokens_expires_at", table_name="download_tokens")
    op.drop_index("ix_download_tokens_digest", table_name="download_tokens")
    op.drop_table("download_tokens")
    op.drop_index("ix_job_logs_job_sequence", table_name="job_logs")
    op.drop_table("job_logs")
    op.drop_index("ix_jobs_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
