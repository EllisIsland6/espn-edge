"""baseline schema

The schema as it stood at the start of Phase 31, before the AI spend ledger.
Cut by autogenerating against models with the two ledger tables removed, so the
next revision could be proven additive rather than asserted to be: revision 0002
detected exactly those two tables and one index and nothing else, which is only
possible if this baseline matches the pre-ledger schema everywhere else.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table('accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(), nullable=False),
    sa.Column('swid', sa.String(), nullable=False),
    sa.Column('espn_s2_encrypted', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('adp_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('pulled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('format', sa.String(), nullable=True),
    sa.Column('teams', sa.Integer(), nullable=True),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('opportunity_imports',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('latest_week', sa.Integer(), nullable=True),
    sa.Column('input_rows', sa.Integer(), nullable=False),
    sa.Column('stored_rows', sa.Integer(), nullable=False),
    sa.Column('matched_players', sa.Integer(), nullable=False),
    sa.Column('unmatched_players', sa.Integer(), nullable=False),
    sa.Column('retries', sa.Integer(), nullable=False),
    sa.Column('package_version', sa.String(), nullable=True),
    sa.Column('schema_fingerprint', sa.String(), nullable=True),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.Column('error_message', sa.String(), nullable=True),
    sa.Column('details_json', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('opportunity_imports', schema=None) as batch_op:
        batch_op.create_index('ix_opportunity_import_season_time', ['season', 'started_at'], unique=False)

    op.create_table('opportunity_weeks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('season_type', sa.String(), nullable=False),
    sa.Column('week', sa.Integer(), nullable=False),
    sa.Column('game_id', sa.String(), nullable=False),
    sa.Column('gsis_id', sa.String(), nullable=False),
    sa.Column('team', sa.String(), nullable=True),
    sa.Column('opponent_team', sa.String(), nullable=True),
    sa.Column('position', sa.String(), nullable=False),
    sa.Column('carries', sa.Float(), nullable=True),
    sa.Column('carry_share', sa.Float(), nullable=True),
    sa.Column('targets', sa.Float(), nullable=True),
    sa.Column('receptions', sa.Float(), nullable=True),
    sa.Column('rushing_yards', sa.Float(), nullable=True),
    sa.Column('receiving_yards', sa.Float(), nullable=True),
    sa.Column('receiving_air_yards', sa.Float(), nullable=True),
    sa.Column('receiving_tds', sa.Float(), nullable=True),
    sa.Column('team_passing_yards', sa.Float(), nullable=True),
    sa.Column('target_share', sa.Float(), nullable=True),
    sa.Column('air_yards_share', sa.Float(), nullable=True),
    sa.Column('wopr', sa.Float(), nullable=True),
    sa.Column('rushing_epa', sa.Float(), nullable=True),
    sa.Column('receiving_epa', sa.Float(), nullable=True),
    sa.Column('fantasy_points_ppr', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('season', 'season_type', 'game_id', 'gsis_id', name='uq_opportunity_player_game')
    )
    with op.batch_alter_table('opportunity_weeks', schema=None) as batch_op:
        batch_op.create_index('ix_opportunity_player_week', ['season', 'gsis_id', 'week'], unique=False)

    op.create_table('players',
    sa.Column('espn_player_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('position', sa.String(), nullable=True),
    sa.Column('nfl_team', sa.String(), nullable=True),
    sa.Column('espn_adp', sa.Float(), nullable=True),
    sa.Column('espn_pct_owned', sa.Float(), nullable=True),
    sa.Column('espn_rank_ppr', sa.Float(), nullable=True),
    sa.Column('proj_ros', sa.Float(), nullable=True),
    sa.Column('ffc_id', sa.Integer(), nullable=True),
    sa.Column('ffc_adp', sa.Float(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('espn_player_id')
    )
    op.create_table('raw_cache',
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('leagues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('espn_league_id', sa.String(), nullable=False),
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('size', sa.Integer(), nullable=True),
    sa.Column('scoring_json', sa.JSON(), nullable=True),
    sa.Column('lineup_slots_json', sa.JSON(), nullable=True),
    sa.Column('draft_type', sa.String(), nullable=True),
    sa.Column('playoff_team_count', sa.Integer(), nullable=True),
    sa.Column('current_scoring_period', sa.Integer(), nullable=True),
    sa.Column('current_matchup_period', sa.Integer(), nullable=True),
    sa.Column('lifecycle', sa.String(), nullable=False),
    sa.Column('my_team_id', sa.Integer(), nullable=True),
    sa.Column('is_public', sa.Boolean(), nullable=False),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sync_ok', sa.Boolean(), nullable=True),
    sa.Column('last_sync_error', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('espn_league_id', 'season', name='uq_league_season')
    )
    op.create_table('nflverse_player_maps',
    sa.Column('espn_player_id', sa.Integer(), nullable=False),
    sa.Column('gsis_id', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('method', sa.String(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['espn_player_id'], ['players.espn_player_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('espn_player_id')
    )
    with op.batch_alter_table('nflverse_player_maps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_nflverse_player_maps_gsis_id'), ['gsis_id'], unique=False)

    op.create_table('ai_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=True),
    sa.Column('scope', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('input_hash', sa.String(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('content_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('current_roster_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('scoring_period', sa.Integer(), nullable=False),
    sa.Column('matchup_period', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', name='uq_current_roster_snapshot_league')
    )
    op.create_table('teams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('espn_team_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('abbrev', sa.String(), nullable=True),
    sa.Column('owner_swids_json', sa.JSON(), nullable=True),
    sa.Column('is_me', sa.Boolean(), nullable=False),
    sa.Column('autodrafted', sa.Boolean(), nullable=False),
    sa.Column('wins', sa.Integer(), nullable=False),
    sa.Column('losses', sa.Integer(), nullable=False),
    sa.Column('ties', sa.Integer(), nullable=False),
    sa.Column('points_for', sa.Float(), nullable=False),
    sa.Column('points_against', sa.Float(), nullable=False),
    sa.Column('standing', sa.Integer(), nullable=True),
    sa.Column('logo_url', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'espn_team_id', name='uq_team_league_espn')
    )
    op.create_table('current_roster_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('snapshot_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('lineup_slot_id', sa.Integer(), nullable=False),
    sa.Column('slot_index', sa.Integer(), nullable=False),
    sa.Column('espn_player_id', sa.Integer(), nullable=False),
    sa.Column('player_name', sa.String(), nullable=True),
    sa.Column('player_position', sa.String(), nullable=True),
    sa.Column('nfl_team', sa.String(), nullable=True),
    sa.Column('opponent', sa.String(), nullable=True),
    sa.Column('kickoff_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('game_status', sa.String(), nullable=True),
    sa.Column('injury_status', sa.String(), nullable=True),
    sa.Column('actual_points', sa.Float(), nullable=True),
    sa.Column('projected_points', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['snapshot_id'], ['current_roster_snapshots.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('snapshot_id', 'team_id', 'lineup_slot_id', 'slot_index', name='uq_current_roster_slot')
    )
    op.create_table('draft_picks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('overall', sa.Integer(), nullable=True),
    sa.Column('round', sa.Integer(), nullable=True),
    sa.Column('round_pick', sa.Integer(), nullable=True),
    sa.Column('team_id', sa.Integer(), nullable=True),
    sa.Column('espn_player_id', sa.Integer(), nullable=True),
    sa.Column('keeper', sa.Boolean(), nullable=False),
    sa.Column('autodraft', sa.Boolean(), nullable=False),
    sa.Column('bid_amount', sa.Integer(), nullable=True),
    sa.Column('adp_at_draft', sa.Float(), nullable=True),
    sa.Column('value_delta', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'overall', name='uq_pick_league_overall')
    )
    op.create_table('lineup_slots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('week', sa.Integer(), nullable=False),
    sa.Column('slot', sa.String(), nullable=True),
    sa.Column('espn_player_id', sa.Integer(), nullable=True),
    sa.Column('points', sa.Float(), nullable=True),
    sa.Column('is_starter', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'week', 'team_id', 'espn_player_id', 'slot', name='uq_lineup')
    )
    op.create_table('matchups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('week', sa.Integer(), nullable=False),
    sa.Column('home_team_id', sa.Integer(), nullable=True),
    sa.Column('away_team_id', sa.Integer(), nullable=True),
    sa.Column('home_points', sa.Float(), nullable=True),
    sa.Column('away_points', sa.Float(), nullable=True),
    sa.Column('home_projected_points', sa.Float(), nullable=True),
    sa.Column('away_projected_points', sa.Float(), nullable=True),
    sa.Column('is_playoff', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['away_team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['home_team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'week', 'home_team_id', 'away_team_id', name='uq_matchup')
    )
    op.create_table('metric_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.String(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('period', sa.Integer(), nullable=False),
    sa.Column('value_float', sa.Float(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'batch_id', 'team_id', 'key', name='uq_metric_snapshot_batch')
    )
    with op.batch_alter_table('metric_snapshots', schema=None) as batch_op:
        batch_op.create_index('ix_metric_snapshot_lookup', ['league_id', 'team_id', 'key', 'period', 'recorded_at'], unique=False)

    op.create_table('metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=True),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('week', sa.Integer(), nullable=True),
    sa.Column('value_float', sa.Float(), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('metrics', schema=None) as batch_op:
        batch_op.create_index('uq_metric_league', ['league_id', 'key'], unique=True, sqlite_where=sa.text('team_id IS NULL AND week IS NULL'), postgresql_where=sa.text('team_id IS NULL AND week IS NULL'))
        batch_op.create_index('uq_metric_league_week', ['league_id', 'key', 'week'], unique=True, sqlite_where=sa.text('team_id IS NULL AND week IS NOT NULL'), postgresql_where=sa.text('team_id IS NULL AND week IS NOT NULL'))
        batch_op.create_index('uq_metric_team', ['league_id', 'team_id', 'key'], unique=True, sqlite_where=sa.text('team_id IS NOT NULL AND week IS NULL'), postgresql_where=sa.text('team_id IS NOT NULL AND week IS NULL'))
        batch_op.create_index('uq_metric_team_week', ['league_id', 'team_id', 'key', 'week'], unique=True, sqlite_where=sa.text('team_id IS NOT NULL AND week IS NOT NULL'), postgresql_where=sa.text('team_id IS NOT NULL AND week IS NOT NULL'))

    op.create_table('transactions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('league_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=True),
    sa.Column('type', sa.String(), nullable=True),
    sa.Column('week', sa.Integer(), nullable=True),
    sa.Column('player_in', sa.Integer(), nullable=True),
    sa.Column('player_out', sa.Integer(), nullable=True),
    sa.Column('bid', sa.Integer(), nullable=True),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('transactions')
    with op.batch_alter_table('metrics', schema=None) as batch_op:
        batch_op.drop_index('uq_metric_team_week', sqlite_where=sa.text('team_id IS NOT NULL AND week IS NOT NULL'), postgresql_where=sa.text('team_id IS NOT NULL AND week IS NOT NULL'))
        batch_op.drop_index('uq_metric_team', sqlite_where=sa.text('team_id IS NOT NULL AND week IS NULL'), postgresql_where=sa.text('team_id IS NOT NULL AND week IS NULL'))
        batch_op.drop_index('uq_metric_league_week', sqlite_where=sa.text('team_id IS NULL AND week IS NOT NULL'), postgresql_where=sa.text('team_id IS NULL AND week IS NOT NULL'))
        batch_op.drop_index('uq_metric_league', sqlite_where=sa.text('team_id IS NULL AND week IS NULL'), postgresql_where=sa.text('team_id IS NULL AND week IS NULL'))

    op.drop_table('metrics')
    with op.batch_alter_table('metric_snapshots', schema=None) as batch_op:
        batch_op.drop_index('ix_metric_snapshot_lookup')

    op.drop_table('metric_snapshots')
    op.drop_table('matchups')
    op.drop_table('lineup_slots')
    op.drop_table('draft_picks')
    op.drop_table('current_roster_entries')
    op.drop_table('teams')
    op.drop_table('current_roster_snapshots')
    op.drop_table('ai_reports')
    with op.batch_alter_table('nflverse_player_maps', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_nflverse_player_maps_gsis_id'))

    op.drop_table('nflverse_player_maps')
    op.drop_table('leagues')
    op.drop_table('raw_cache')
    op.drop_table('players')
    with op.batch_alter_table('opportunity_weeks', schema=None) as batch_op:
        batch_op.drop_index('ix_opportunity_player_week')

    op.drop_table('opportunity_weeks')
    with op.batch_alter_table('opportunity_imports', schema=None) as batch_op:
        batch_op.drop_index('ix_opportunity_import_season_time')

    op.drop_table('opportunity_imports')
    op.drop_table('adp_snapshots')
    op.drop_table('accounts')
