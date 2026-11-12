"""add ai_provider.max_tokens

The reply length a request through a provider asks for when the caller names none, so a
person who wants long replies sets it once per provider instead of living with the
gateway's 16k. Null for every existing provider, which keeps today's behaviour.

Additive only; downgrading drops the column and the values set in it.

Revision ID: 8c4f2d1e7a93
Revises: 5d2e9b7c1a40
Create Date: 2026-11-12 09:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c4f2d1e7a93'
down_revision: Union[str, None] = '5d2e9b7c1a40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ai_provider') as batch:
        batch.add_column(sa.Column('max_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ai_provider') as batch:
        batch.drop_column('max_tokens')
