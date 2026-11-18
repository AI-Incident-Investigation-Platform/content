"""Initial content schema.

Revision ID: 0001_content
Revises:
"""

from alembic import op

from src.core.db.models import Base

revision = "0001_content"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    raise NotImplementedError("Dropping content tables would destroy documents and files")
