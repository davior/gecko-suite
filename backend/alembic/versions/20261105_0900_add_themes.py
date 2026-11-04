"""add themes and preferences (M7)

Two new tables: the theme library (Notes' theme shape, scoped like the registry) and a
key-value table of each person's preferences, where each app's chosen theme lives
(`theme.<app>`).

Additive only; downgrading drops both tables (and every theme made here).

Revision ID: 5d2e9b7c1a40
Revises: 3f1c8a2b6d74
Create Date: 2026-11-05 09:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '5d2e9b7c1a40'
down_revision: Union[str, None] = '3f1c8a2b6d74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('theme',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('bg_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('bg_color1', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('bg_color2', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('bg_image_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('bg_image_mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('bg_blur', sa.Float(), nullable=False),
    sa.Column('glass_opacity', sa.Float(), nullable=False),
    sa.Column('glass_blur', sa.Float(), nullable=False),
    sa.Column('shadow_size', sa.Float(), nullable=False),
    sa.Column('shadow_blur', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_theme_scope', 'theme', ['scope'], unique=False)
    op.create_index('ix_theme_user_id', 'theme', ['user_id'], unique=False)

    op.create_table('user_preference',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'key')
    )
    op.create_index('ix_user_preference_user_id', 'user_preference', ['user_id'], unique=False)
    op.create_index('ix_user_preference_key', 'user_preference', ['key'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_preference_key', table_name='user_preference')
    op.drop_index('ix_user_preference_user_id', table_name='user_preference')
    op.drop_table('user_preference')
    op.drop_index('ix_theme_user_id', table_name='theme')
    op.drop_index('ix_theme_scope', table_name='theme')
    op.drop_table('theme')
