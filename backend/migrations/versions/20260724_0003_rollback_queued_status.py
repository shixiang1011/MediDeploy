"""Add an explicit queue state for operator-requested rollback retries.

Revision ID: 20260724_0003
Revises: 20260724_0002
"""
from alembic import op


revision = "20260724_0003"
down_revision = "20260724_0002"
branch_labels = None
depends_on = None


TASK_STATUSES_WITH_ROLLBACK_QUEUE = (
    "DRAFT",
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "ROLLBACK_QUEUED",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "CANCELLED",
)
TASK_STATUSES_WITHOUT_ROLLBACK_QUEUE = tuple(
    value for value in TASK_STATUSES_WITH_ROLLBACK_QUEUE if value != "ROLLBACK_QUEUED"
)


def enum_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deployments MODIFY COLUMN status "
        f"ENUM({enum_values(TASK_STATUSES_WITH_ROLLBACK_QUEUE)}) NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE deployments SET status='FAILED' WHERE status='ROLLBACK_QUEUED'"
    )
    op.execute(
        "ALTER TABLE deployments MODIFY COLUMN status "
        f"ENUM({enum_values(TASK_STATUSES_WITHOUT_ROLLBACK_QUEUE)}) NOT NULL"
    )
