CREATE INDEX idx_objects_current_geom ON objects_current USING gist (geom);
CREATE INDEX idx_objects_history_geom ON objects_history USING gist (geom);
CREATE INDEX idx_objects_current_kind ON objects_current (kind);
CREATE INDEX idx_objects_current_ts ON objects_current (ts DESC);
