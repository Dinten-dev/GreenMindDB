CREATE TABLE IF NOT EXISTS direct_visual_segment (
 segment_id varchar(36) PRIMARY KEY REFERENCES direct_segment(id) ON DELETE CASCADE,
 source_revision integer NOT NULL, seconds integer NOT NULL CHECK(seconds IN (1,60,600)),
 source text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), error text,
 retry_at double precision NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS direct_visual_point (
 segment_id varchar(36) NOT NULL REFERENCES direct_visual_segment(segment_id) ON DELETE CASCADE,
 device_id varchar(36) NOT NULL, bucket bigint NOT NULL, channel integer NOT NULL,
 unit text NOT NULL, seconds integer NOT NULL, n bigint NOT NULL CHECK(n>0),
 total double precision NOT NULL, total2 double precision NOT NULL,
 minimum double precision NOT NULL, maximum double precision NOT NULL,
 duration double precision NOT NULL,
 PRIMARY KEY(segment_id,channel,bucket)
);
CREATE INDEX IF NOT EXISTS direct_visual_point_device_time ON direct_visual_point(device_id,bucket);
CREATE TABLE IF NOT EXISTS direct_visual_worker (
 id integer PRIMARY KEY, status text NOT NULL, updated_at timestamptz NOT NULL
);
