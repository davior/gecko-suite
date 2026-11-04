"""add accounts: sign-in moves here from gecko-notes (M6)

The user table grows the account columns gecko-notes' had (a password hash, active,
email-verified, two-factor method and secret, last sign-in), and two tables arrive: the
single-use secrets behind email verification, password reset and emailed sign-in codes,
and the instance-wide sign-up switches.

Additive only. Every existing row becomes a shadow with no password, which cannot sign in
here until the importer brings that person's Notes account across. Downgrading drops the
new tables and columns (and with them any account set up here).

Revision ID: 3f1c8a2b6d74
Revises: 7be2a41c9d05
Create Date: 2026-11-04 13:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '3f1c8a2b6d74'
down_revision: Union[str, None] = '7be2a41c9d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('hashed_password', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('two_factor_method', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column('totp_secret', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column('last_login', sa.DateTime(), nullable=True))
        batch.create_index('ix_user_email', ['email'], unique=False)

    op.create_table('auth_token',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('purpose', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_auth_token_user_id', 'auth_token', ['user_id'], unique=False)
    op.create_index('ix_auth_token_purpose', 'auth_token', ['purpose'], unique=False)
    op.create_index('ix_auth_token_token_hash', 'auth_token', ['token_hash'], unique=False)

    op.create_table('instance_setting',
    sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('instance_setting')
    op.drop_index('ix_auth_token_token_hash', table_name='auth_token')
    op.drop_index('ix_auth_token_purpose', table_name='auth_token')
    op.drop_index('ix_auth_token_user_id', table_name='auth_token')
    op.drop_table('auth_token')
    with op.batch_alter_table('user') as batch:
        batch.drop_index('ix_user_email')
        for column in ('last_login', 'totp_secret', 'two_factor_method', 'email_verified', 'is_active',
                       'hashed_password'):
            batch.drop_column(column)
