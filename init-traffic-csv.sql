CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS traffic_gps (
    time          TIMESTAMPTZ NOT NULL,
    vehicle_id    VARCHAR(32) NOT NULL,
    checkpoint_id VARCHAR(32) NOT NULL,
    gps_lng       DOUBLE PRECISION,
    gps_lat       DOUBLE PRECISION,
    speed_kmh     DOUBLE PRECISION
);

SELECT create_hypertable('traffic_gps', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_traffic_vehicle_time ON traffic_gps (vehicle_id, time DESC);