-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE objects_current (
  id text PRIMARY KEY,
  kind text NOT NULL,        -- 'aircraft', 'satellite', 'debris', 'ship'
  source text NOT NULL,      -- 'tar1090', 'opensky', 'celestrak'
  ts timestamptz NOT NULL,
  lat double precision NOT NULL,
  lon double precision NOT NULL,
  alt_m double precision,
  heading_deg double precision,
  speed_mps double precision,
  meta jsonb,
  geom geography(point, 4326) NOT NULL
);

CREATE TABLE objects_history (
  id text NOT NULL,
  kind text NOT NULL,
  source text NOT NULL,
  ts timestamptz NOT NULL,
  lat double precision NOT NULL,
  lon double precision NOT NULL,
  alt_m double precision,
  heading_deg double precision,
  speed_mps double precision,
  meta jsonb,
  geom geography(point, 4326) NOT NULL
);

CREATE TABLE events (
  event_id text PRIMARY KEY,
  kind text NOT NULL,
  ts timestamptz NOT NULL,
  geom geography(point, 4326),
  meta jsonb
);

CREATE TABLE sat_catalog (
  norad_id text PRIMARY KEY,
  name text NOT NULL,
  tle1 text NOT NULL,
  tle2 text NOT NULL,
  last_updated timestamptz NOT NULL
);
