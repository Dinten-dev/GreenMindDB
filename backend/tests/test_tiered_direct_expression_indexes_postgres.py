"""Prepared opt-in PG15 synthetic proof; never connects without an explicit URL."""

import ast
import inspect
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.tiered_archive.recovery import restore_url
from app.tiered_archive.source import DATASETS, Source

pytestmark = pytest.mark.integration
DEPLOY = Path(__file__).resolve().parents[2] / "deploy/tiered-archive"


def query(spec, cursor=""):
    tree = ast.parse(inspect.getsource(Source))
    method = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "export"
    )
    value = next(
        n.value.args[0]
        for n in ast.walk(method)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "query" for t in n.targets)
    )
    return eval(
        compile(ast.Expression(value), "existing_Source.export", "eval"),
        {"__builtins__": {}},
        {
            "spec": spec,
            "stamp": "to_timestamp(" + spec.time + ")",
            "order": ",".join("t." + key for key in spec.keys),
            "cursor": cursor,
        },
    )


def collect_indexes(value):
    result = []
    if isinstance(value, dict):
        if "Index Name" in value:
            result.append(value["Index Name"])
        for child in value.values():
            result.extend(collect_indexes(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(collect_indexes(child))
    return result


def test_pg15_synthetic_expression_indexes_and_unchanged_pages():
    raw = os.getenv("GREENMIND_DIRECT_INDEX_PG15_TEST_URL")
    if not raw:
        pytest.skip("Explicit fresh local PG15 _restore_ test database required")
    url = restore_url(raw)
    assert re.fullmatch(r"gm_restore_direct_indexes_[a-f0-9]{16}", url.database)
    engine = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as db:
            assert 150000 <= int(db.execute(text("SHOW server_version_num")).scalar_one()) < 160000
            assert not db.execute(
                text(
                    "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' LIMIT 1"
                )
            ).first()
            db.execute(text("SET statement_timeout='8000ms'"))
            db.execute(text("SET lock_timeout='150ms'"))
            db.execute(text("SET search_path=pg_catalog,public"))
            for command in (
                "CREATE TABLE public.direct_device(id text PRIMARY KEY,organization_id text)",
                "CREATE TABLE public.direct_segment(id text PRIMARY KEY,device_id text)",
                "CREATE TABLE public.direct_chunk(id text PRIMARY KEY,device_id text,received_at double precision,payload bytea)",
                "CREATE TABLE public.direct_revision(segment_id text,revision bigint,verified_at double precision,raw_deleted_at double precision,PRIMARY KEY(segment_id,revision))",
                "CREATE TABLE public.direct_visual_point(segment_id text,device_id text,channel integer,bucket bigint,PRIMARY KEY(segment_id,channel,bucket))",
                "INSERT INTO public.direct_device VALUES('device','11111111-1111-1111-1111-111111111111')",
                "INSERT INTO public.direct_segment VALUES('segment','device')",
                "INSERT INTO public.direct_chunk SELECT i::text,'device',1700000000+i,CASE WHEN i%2=0 THEN decode('00ff','hex') ELSE NULL END FROM generate_series(1,50000) i",
                "INSERT INTO public.direct_revision SELECT 'segment',i,1700000000+i,CASE WHEN i%2=0 THEN 1700000000+i ELSE NULL END FROM generate_series(1,50000) i",
                "INSERT INTO public.direct_visual_point SELECT 'segment','device',i%2,1700000000+i FROM generate_series(1,50000) i",
            ):
                db.execute(text(command))
            guard_file = (DEPLOY / "direct-indexes-check.sql").read_text()
            guard = "DO $guard$" + guard_file.split("DO $guard$", 1)[1]
            db.execute(
                text("SELECT set_config('greenmind.direct_index_require_all','false',false)")
            )
            db.execute(text(guard))
            start = datetime.fromtimestamp(1700010000, UTC)
            parameters = {
                "org": "11111111-1111-1111-1111-111111111111",
                "start": start,
                "end": start + timedelta(seconds=60),
                "lim": 32,
            }
            before = {
                name: db.execute(text(query(DATASETS[name])), parameters).mappings().all()
                for name in ("direct_chunk", "direct_revision", "direct_visual_point")
            }
            for line in (DEPLOY / "direct-indexes.sql").read_text().splitlines():
                if line.startswith("CREATE INDEX CONCURRENTLY"):
                    db.execute(text(line))
            db.execute(text("SELECT set_config('greenmind.direct_index_require_all','true',false)"))
            db.execute(text(guard))
            for name in ("direct_device", "direct_segment"):
                db.execute(text("ANALYZE public." + name))
            for name in before:
                db.execute(text("ANALYZE public." + name))
                sql = query(DATASETS[name])
                after = db.execute(text(sql), parameters).mappings().all()
                assert before[name] == after and len(after) == 32
                plan = db.execute(
                    text("EXPLAIN (FORMAT JSON, ANALYZE FALSE) " + sql), parameters
                ).scalar_one()
                assert "tiered_" + name + "_scan" in collect_indexes(plan)
            # Deliberately retain only this caller-supplied fresh test database.
            # No DROP, production cleanup, image pull or remote target is invoked.
    finally:
        engine.dispose()
