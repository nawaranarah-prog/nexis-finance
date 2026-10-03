"""pulse personas, source events, holdings and notifications

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03 21:26:16.390088
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table('source_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('url', sa.String(length=1500), nullable=True),
    sa.Column('publisher', sa.String(length=160), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=False),
    sa.Column('facts', sa.JSON(), nullable=False),
    sa.Column('assets', sa.JSON(), nullable=False),
    sa.Column('topics', sa.JSON(), nullable=False),
    sa.Column('importance', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('external_key', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_events')),
    sa.UniqueConstraint('external_key', name=op.f('uq_source_events_external_key'))
    )
    with op.batch_alter_table('source_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_source_events_kind'), ['kind'], unique=False)
        batch_op.create_index('ix_source_events_status_published', ['status', 'published_at'], unique=False)

    op.create_table('alert_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=True),
    sa.Column('event_type', sa.String(length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_alert_rules_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alert_rules')),
    sa.UniqueConstraint('user_id', 'symbol', 'event_type', name='uq_alert_rules_user_symbol_event')
    )
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alert_rules_user_id'), ['user_id'], unique=False)

    op.create_table('digest_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('period', sa.String(length=8), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('detail', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_digest_records_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_digest_records'))
    )
    with op.batch_alter_table('digest_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_digest_records_user_id'), ['user_id'], unique=False)

    op.create_table('notification_preferences',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('channels', sa.JSON(), nullable=False),
    sa.Column('email_enabled', sa.Boolean(), nullable=False),
    sa.Column('price_move_pct', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_notification_preferences'))
    )
    op.create_table('personas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('archetype', sa.String(length=40), nullable=False),
    sa.Column('traits', sa.JSON(), nullable=False),
    sa.Column('memory', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('last_active_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_personas_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_personas')),
    sa.UniqueConstraint('user_id', name=op.f('uq_personas_user_id'))
    )
    with op.batch_alter_table('personas', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_personas_archetype'), ['archetype'], unique=False)

    op.create_table('user_holdings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('portfolio', sa.String(length=60), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=True),
    sa.Column('asset_type', sa.String(length=12), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('purchase_price', sa.Float(), nullable=True),
    sa.Column('purchase_date', sa.Date(), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_holdings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_holdings'))
    )
    with op.batch_alter_table('user_holdings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_holdings_user_id'), ['user_id'], unique=False)

    op.create_table('user_notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=16), nullable=False),
    sa.Column('event_type', sa.String(length=16), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('source_name', sa.String(length=160), nullable=True),
    sa.Column('source_url', sa.String(length=1500), nullable=True),
    sa.Column('link', sa.String(length=300), nullable=True),
    sa.Column('event_id', sa.Integer(), nullable=True),
    sa.Column('dedupe_key', sa.String(length=120), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.Column('digested_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['source_events.id'], name=op.f('fk_user_notifications_event_id_source_events'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_notifications')),
    sa.UniqueConstraint('user_id', 'dedupe_key', name='uq_user_notifications_dedupe')
    )
    with op.batch_alter_table('user_notifications', schema=None) as batch_op:
        batch_op.create_index('ix_user_notifications_user_read', ['user_id', 'read_at'], unique=False)

    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('generated', sa.Boolean(), server_default='0', nullable=False))

    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('event_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_posts_event_id'), ['event_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_posts_event_id_source_events'), 'source_events', ['event_id'], ['id'], ondelete='SET NULL')




def downgrade() -> None:

    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_posts_event_id_source_events'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_posts_event_id'))
        batch_op.drop_column('event_id')

    with op.batch_alter_table('comments', schema=None) as batch_op:
        batch_op.drop_column('generated')

    with op.batch_alter_table('user_notifications', schema=None) as batch_op:
        batch_op.drop_index('ix_user_notifications_user_read')

    op.drop_table('user_notifications')
    with op.batch_alter_table('user_holdings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_holdings_user_id'))

    op.drop_table('user_holdings')
    with op.batch_alter_table('personas', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_personas_archetype'))

    op.drop_table('personas')
    op.drop_table('notification_preferences')
    with op.batch_alter_table('digest_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_digest_records_user_id'))

    op.drop_table('digest_records')
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alert_rules_user_id'))

    op.drop_table('alert_rules')
    with op.batch_alter_table('source_events', schema=None) as batch_op:
        batch_op.drop_index('ix_source_events_status_published')
        batch_op.drop_index(batch_op.f('ix_source_events_kind'))

    op.drop_table('source_events')

