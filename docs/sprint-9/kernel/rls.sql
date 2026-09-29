-- Transaction-local tenant context. `current_setting('app.tenant_id', true)`
-- reads whatever the transaction set with SET LOCAL; a missing setting is NULL,
-- and every policy below compares against it, so NO CONTEXT = NO ROWS. Failing
-- closed is the property the R4 "absent context" attack checks.
CREATE OR REPLACE FUNCTION current_tenant() RETURNS bigint AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')::bigint;
$$ LANGUAGE sql STABLE;

ALTER TABLE tenants     ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants     FORCE  ROW LEVEL SECURITY;
ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships FORCE  ROW LEVEL SECURITY;
ALTER TABLE leagues     ENABLE ROW LEVEL SECURITY;
ALTER TABLE leagues     FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS t_tenants ON tenants;
CREATE POLICY t_tenants ON tenants USING (id = current_tenant());

DROP POLICY IF EXISTS t_memberships ON memberships;
CREATE POLICY t_memberships ON memberships USING (tenant_id = current_tenant());

-- ALL, not just SELECT: a write policy is what stops a tenant INSERTing a row
-- into another tenant, which SELECT-only policies silently permit.
DROP POLICY IF EXISTS t_leagues ON leagues;
CREATE POLICY t_leagues ON leagues USING (tenant_id = current_tenant())
                                   WITH CHECK (tenant_id = current_tenant());

ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_teams ON teams;
CREATE POLICY t_teams ON teams USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE draft_picks ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_picks FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_draft_picks ON draft_picks;
CREATE POLICY t_draft_picks ON draft_picks USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE metrics FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_metrics ON metrics;
CREATE POLICY t_metrics ON metrics USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE matchups ENABLE ROW LEVEL SECURITY;
ALTER TABLE matchups FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_matchups ON matchups;
CREATE POLICY t_matchups ON matchups USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_transactions ON transactions;
CREATE POLICY t_transactions ON transactions USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE lineup_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE lineup_slots FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_lineup_slots ON lineup_slots;
CREATE POLICY t_lineup_slots ON lineup_slots USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE current_roster_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE current_roster_snapshots FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_current_roster_snapshots ON current_roster_snapshots;
CREATE POLICY t_current_roster_snapshots ON current_roster_snapshots USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE current_roster_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE current_roster_entries FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_current_roster_entries ON current_roster_entries;
CREATE POLICY t_current_roster_entries ON current_roster_entries USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE metric_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_snapshots FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_metric_snapshots ON metric_snapshots;
CREATE POLICY t_metric_snapshots ON metric_snapshots USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

ALTER TABLE ai_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_reports FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS t_ai_reports ON ai_reports;
CREATE POLICY t_ai_reports ON ai_reports USING (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
) WITH CHECK (
  league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())
);

-- Non-owner runtime roles. NOSUPERUSER and NOBYPASSRLS are explicit because
-- Phase 33 measured that FORCE ROW LEVEL SECURITY constrains the table OWNER and
-- NOT a superuser or a BYPASSRLS role: policies all correct, isolation silently
-- off. The app must never connect as the owner.
DROP ROLE IF EXISTS edge_app;
DROP ROLE IF EXISTS edge_worker;
CREATE ROLE edge_app    LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE edge_worker LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA public TO edge_app, edge_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO edge_app, edge_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO edge_app, edge_worker;
-- No DDL: Alembic runs as the owner, the app never shapes the schema.
REVOKE CREATE ON SCHEMA public FROM edge_app, edge_worker;
