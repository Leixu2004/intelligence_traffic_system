CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS traffic_gps (
    time          TIMESTAMPTZ NOT NULL,
    vehicle_id    VARCHAR(64) NOT NULL,
    checkpoint_id VARCHAR(64) NOT NULL,
    camera_id     VARCHAR(64) NOT NULL,
    gps_lng       DOUBLE PRECISION,
    gps_lat       DOUBLE PRECISION,
    speed_kmh     DOUBLE PRECISION,
    vehicle_type  VARCHAR(32),
    confidence    REAL,
    bbox_x1       INTEGER,
    bbox_y1       INTEGER,
    bbox_x2       INTEGER,
    bbox_y2       INTEGER,
    PRIMARY KEY (time, vehicle_id)
);

SELECT create_hypertable(
    'traffic_gps', 'time',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_traffic_gps_checkpoint_time
    ON traffic_gps (checkpoint_id, time DESC);

CREATE TABLE IF NOT EXISTS traffic_violations (
    time           TIMESTAMPTZ NOT NULL,
    event_id       UUID NOT NULL,
    plate          VARCHAR(32) NOT NULL,
    violation_type VARCHAR(64) NOT NULL,
    checkpoint_id  VARCHAR(64) NOT NULL,
    camera_id      VARCHAR(64) NOT NULL,
    image_path     TEXT,
    track_id       VARCHAR(64),
    vehicle_type   VARCHAR(32),
    confidence     REAL,
    bbox_x1        INTEGER,
    bbox_y1        INTEGER,
    bbox_x2        INTEGER,
    bbox_y2        INTEGER,
    PRIMARY KEY (time, event_id)
);

SELECT create_hypertable(
    'traffic_violations', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_traffic_violations_plate_time
    ON traffic_violations (plate, time DESC);

CREATE TABLE IF NOT EXISTS plate_recognitions (
    time                TIMESTAMPTZ NOT NULL,
    event_id            UUID NOT NULL,
    plate               VARCHAR(32) NOT NULL,
    checkpoint_id       VARCHAR(64) NOT NULL,
    camera_id           VARCHAR(64) NOT NULL,
    image_path          TEXT,
    track_id            VARCHAR(64),
    vehicle_type        VARCHAR(32),
    ocr_confidence      REAL,
    detector_confidence REAL,
    bbox_x1             INTEGER,
    bbox_y1             INTEGER,
    bbox_x2             INTEGER,
    bbox_y2             INTEGER,
    PRIMARY KEY (time, event_id)
);

SELECT create_hypertable(
    'plate_recognitions', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_plate_recognitions_plate_time
    ON plate_recognitions (plate, time DESC);

CREATE INDEX IF NOT EXISTS idx_plate_recognitions_checkpoint_time
    ON plate_recognitions (checkpoint_id, time DESC);

CREATE MATERIALIZED VIEW IF NOT EXISTS checkpoint_traffic_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 minute', time) AS bucket,
    checkpoint_id,
    COUNT(*) AS total_vehicles,
    AVG(speed_kmh) AS avg_speed
FROM traffic_gps
GROUP BY bucket, checkpoint_id
WITH NO DATA;

DO $$
BEGIN
    PERFORM add_continuous_aggregate_policy(
        'checkpoint_traffic_1m',
        start_offset => INTERVAL '3 hours',
        end_offset => INTERVAL '1 minute',
        schedule_interval => INTERVAL '1 minute'
    );
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;

DO $$
BEGIN
    PERFORM add_retention_policy('traffic_gps', INTERVAL '30 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;

DO $$
BEGIN
    PERFORM add_retention_policy('traffic_violations', INTERVAL '90 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;

DO $$
BEGIN
    PERFORM add_retention_policy('plate_recognitions', INTERVAL '90 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;
