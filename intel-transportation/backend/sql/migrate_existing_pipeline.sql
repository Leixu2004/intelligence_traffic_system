BEGIN;

-- 保留旧版 traffic_gps 数据，只补齐当前消费者所需字段。
ALTER TABLE public.traffic_gps
    ADD COLUMN IF NOT EXISTS camera_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS vehicle_type VARCHAR(32),
    ADD COLUMN IF NOT EXISTS confidence REAL,
    ADD COLUMN IF NOT EXISTS bbox_x1 INTEGER,
    ADD COLUMN IF NOT EXISTS bbox_y1 INTEGER,
    ADD COLUMN IF NOT EXISTS bbox_x2 INTEGER,
    ADD COLUMN IF NOT EXISTS bbox_y2 INTEGER;

UPDATE public.traffic_gps
SET camera_id = checkpoint_id
WHERE camera_id IS NULL;

ALTER TABLE public.traffic_gps
    ALTER COLUMN camera_id SET DEFAULT 'UNKNOWN',
    ALTER COLUMN camera_id SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.traffic_gps
        GROUP BY time, vehicle_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'traffic_gps 存在重复 (time, vehicle_id)，无法建立幂等写入约束';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_traffic_gps_time_vehicle_id
    ON public.traffic_gps (time, vehicle_id);
CREATE INDEX IF NOT EXISTS idx_traffic_gps_checkpoint_time
    ON public.traffic_gps (checkpoint_id, time DESC);

CREATE TABLE IF NOT EXISTS public.traffic_violations (
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
    'public.traffic_violations',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_traffic_violations_plate_time
    ON public.traffic_violations (plate, time DESC);
CREATE INDEX IF NOT EXISTS idx_traffic_violations_checkpoint_time
    ON public.traffic_violations (checkpoint_id, time DESC);

DO $$
BEGIN
    PERFORM add_retention_policy('public.traffic_violations', INTERVAL '90 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;

CREATE TABLE IF NOT EXISTS public.plate_recognitions (
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
    'public.plate_recognitions',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_plate_recognitions_plate_time
    ON public.plate_recognitions (plate, time DESC);
CREATE INDEX IF NOT EXISTS idx_plate_recognitions_checkpoint_time
    ON public.plate_recognitions (checkpoint_id, time DESC);

DO $$
BEGIN
    PERFORM add_retention_policy('public.plate_recognitions', INTERVAL '90 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;

COMMIT;
