"""add the media job table

A fal.ai request the suite is running for an app (M3): who asked, which endpoint, fal's
queue ids and, once fetched, the result. Nothing existing changes.

Revision ID: 7be2a41c9d05
Revises: 5364cbc1555c
Create Date: 2026-11-03 09:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '7be2a41c9d05'
down_revision: Union[str, None] = '5364cbc1555c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('media_job',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('app', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('endpoint_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('provider_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('provider_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('subject_ref', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('request', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('queue_position', sa.Integer(), nullable=True),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('result', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('billable_units', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('usage_event_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('media_job', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_media_job_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_media_job_app'), ['app'], unique=False)
        batch_op.create_index(batch_op.f('ix_media_job_subject_ref'), ['subject_ref'], unique=False)
        batch_op.create_index(batch_op.f('ix_media_job_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('media_job', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_media_job_status'))
        batch_op.drop_index(batch_op.f('ix_media_job_subject_ref'))
        batch_op.drop_index(batch_op.f('ix_media_job_app'))
        batch_op.drop_index(batch_op.f('ix_media_job_user_id'))
    op.drop_table('media_job')
