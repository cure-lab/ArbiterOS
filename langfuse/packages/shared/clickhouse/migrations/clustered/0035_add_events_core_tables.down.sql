DROP VIEW IF EXISTS events_full_mv ON CLUSTER default;
DROP VIEW IF EXISTS events_core_mv ON CLUSTER default;

DROP TABLE IF EXISTS events_core ON CLUSTER default;
DROP TABLE IF EXISTS events_full ON CLUSTER default;
