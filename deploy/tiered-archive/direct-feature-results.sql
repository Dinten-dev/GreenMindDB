-- Candidate only: run through the controlled schema deployment and grant review.
-- This file is not a deployment instruction or approval.
BEGIN;
CREATE TABLE direct_feature_result (
 id varchar(36) PRIMARY KEY, segment_id varchar(36) NOT NULL, revision integer NOT NULL,
 manifest_sha256 varchar(64) NOT NULL, input_sha256 varchar(64) NOT NULL UNIQUE,
 inputs json NOT NULL, payload json NOT NULL, payload_sha256 varchar(64) NOT NULL,
 created_at double precision NOT NULL, verified_at double precision,
 FOREIGN KEY (segment_id, revision) REFERENCES direct_revision(segment_id, revision),
 CHECK (revision > 0), CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
 CHECK (input_sha256 ~ '^[0-9a-f]{64}$'), CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE TABLE direct_feature_job (
 id varchar(36) PRIMARY KEY, target_sha256 varchar(64) NOT NULL UNIQUE, target json NOT NULL,
 status varchar(16) NOT NULL, claim_token varchar(36), claim_until double precision,
 retry_at double precision NOT NULL, attempts integer NOT NULL, error varchar(128),
 created_at double precision NOT NULL,
 CHECK (target_sha256 ~ '^[0-9a-f]{64}$'), CHECK (attempts >= 0),
 CHECK (status IN ('pending', 'claimed', 'done', 'failed'))
);
-- Restrict mutation; readback may only transition verified_at from NULL once.
CREATE FUNCTION direct_feature_result_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'direct result is append-only'; END IF;
 IF OLD.verified_at IS NOT NULL OR NEW.verified_at IS NULL OR
    (to_jsonb(OLD) - 'verified_at') IS DISTINCT FROM (to_jsonb(NEW) - 'verified_at') THEN
   RAISE EXCEPTION 'direct result is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER direct_feature_result_immutable BEFORE UPDATE OR DELETE ON direct_feature_result
 FOR EACH ROW EXECUTE FUNCTION direct_feature_result_immutable();
COMMIT;
