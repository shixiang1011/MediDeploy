"""Add a draft state so deployments start only after operator confirmation.

Revision ID: 20260724_0002
Revises: 20260724_0001
"""
from alembic import op


revision = "20260724_0002"
down_revision = "20260724_0001"
branch_labels = None
depends_on = None


TASK_STATUSES_WITH_DRAFT = (
    "DRAFT",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "CANCELLED",
)
TASK_STATUSES_WITHOUT_DRAFT = TASK_STATUSES_WITH_DRAFT[1:]


def enum_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deployments MODIFY COLUMN status "
        f"ENUM({enum_values(TASK_STATUSES_WITH_DRAFT)}) NOT NULL"
    )


def downgrade() -> None:
    op.execute("UPDATE deployments SET status='CANCELLED' WHERE status='DRAFT'")
    op.execute(
        "ALTER TABLE deployments MODIFY COLUMN status "
        f"ENUM({enum_values(TASK_STATUSES_WITHOUT_DRAFT)}) NOT NULL"
    )
