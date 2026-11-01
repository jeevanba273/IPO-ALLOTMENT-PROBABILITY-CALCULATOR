-- 0005_dashboard_trigger.sql: jobs can also be started from the dashboard's Health page.

ALTER TABLE job_runs DROP CONSTRAINT job_runs_trigger_check;
ALTER TABLE job_runs ADD CONSTRAINT job_runs_trigger_check
    CHECK (trigger IN ('schedule', 'startup', 'cli', 'dashboard'));
