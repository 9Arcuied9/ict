CREATE SCHEMA IF NOT EXISTS inspection;

CREATE TABLE IF NOT EXISTS inspection.defects (
    defect_id bigint PRIMARY KEY CHECK (defect_id > 0),
    damage_class text NOT NULL CHECK (btrim(damage_class) <> ''),
    lat double precision CHECK (lat BETWEEN -90 AND 90),
    lon double precision CHECK (lon BETWEEN -180 AND 180),
    first_seen timestamptz,
    last_seen timestamptz,
    observation_count integer NOT NULL CHECK (observation_count > 0),
    heading double precision CHECK (heading BETWEEN 0 AND 360),
    road_side text,
    crop text,
    CHECK ((lat IS NULL) = (lon IS NULL)),
    CHECK (first_seen <= last_seen)
);

CREATE TABLE IF NOT EXISTS inspection.observations (
    defect_id bigint NOT NULL REFERENCES inspection.defects (defect_id),
    trip_id text NOT NULL CHECK (btrim(trip_id) <> ''),
    event_id text NOT NULL CHECK (btrim(event_id) <> ''),
    damage_class text NOT NULL CHECK (btrim(damage_class) <> ''),
    captured_at timestamptz,
    lat double precision CHECK (lat BETWEEN -90 AND 90),
    lon double precision CHECK (lon BETWEEN -180 AND 180),
    peak_s double precision CHECK (peak_s >= 0 AND peak_s < 'Infinity'::float8),
    gps_quality text,
    decision text,
    PRIMARY KEY (defect_id, trip_id),
    CHECK ((lat IS NULL) = (lon IS NULL))
);

CREATE TABLE IF NOT EXISTS inspection.images (
    defect_id bigint PRIMARY KEY REFERENCES inspection.defects (defect_id),
    crop text NOT NULL,
    jpeg bytea NOT NULL
);
