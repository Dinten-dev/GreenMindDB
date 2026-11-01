"""Explicit PG15 origin-only authority for one private, isolated restore DB.

This does not grant/revoke privileges or change any role/GUC. DB owners/admins
remain trusted; equivalence is only for admitted ordinary, unprivileged DML.
Privileged callable definers have no implicit extension allowlist.
"""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.raw_archive.policy import ArchiveBlocked

from .codec import digest

MODE = "timescale-origin-guarded-v1"
FORMAT = "isolated-restore-origin-capability-v1"
_DANGEROUS = {
    "pg_execute_server_program",
    "pg_write_server_files",
    "pg_read_server_files",
    "pg_signal_backend",
    "pg_checkpoint",
}
_FLAGS = ("superuser", "create_role", "create_db", "replication", "bypass_rls")
_CONFIG = {"session_replication_role", "role", "session_authorization"}
_DDL_TAGS = {
    "ALTER TABLE",
    "CREATE TRIGGER",
    "CREATE TABLE",
    "CREATE INDEX",
    "ALTER INDEX",
    "DROP TABLE",
    "DROP INDEX",
    "DROP SCHEMA",
}


def _need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def _closure(seed, memberships):
    result = {seed}
    while True:
        grown = result | {m["role"] for m in memberships if m["member"] in result}
        if grown == result:
            return result
        _need(len(grown) <= 128, "Origin capability role budget exceeded")
        result = grown


def validate(facts):
    """Validate safe metadata and return the stable independent authority pin."""
    db = facts["database"]
    _need(
        150000 <= db["version"] < 160000 and db["recovery"] is False,
        "Origin capability requires the verified PG15 primary",
    )
    _need(
        db["actor"] == db["session"] == db["owner"] and db["origin"] == "origin",
        "Origin capability requires the unimpersonated restore database owner",
    )
    _need(
        db["public_connect"] is False and db["public_temp"] is False,
        "Origin capability requires private database CONNECT and TEMP ACLs",
    )
    _need(
        facts["other_clients"] is False and facts["subscriptions"] is False,
        "Origin capability forbids application clients and subscriptions",
    )
    _need(
        facts["scheduled_jobs"] is False,
        "Origin capability forbids scheduled background jobs",
    )
    _need(
        0 < len(facts["extensions"]) <= 32
        and any(
            e["name"] == "timescaledb" and e["version"] == "2.17.2" for e in facts["extensions"]
        ),
        "Origin capability requires the reviewed Timescale 2.17.2 extension",
    )
    roles = {r["oid"]: r for r in facts["roles"]}
    _need(
        _events_valid(facts["events"], facts["extensions"], roles),
        "Origin capability event trigger provenance changed",
    )
    _need(
        0 < len(roles) == len(facts["roles"]) <= 128
        and len(facts["memberships"]) <= 256
        and db["actor"] in roles,
        "Origin capability role inventory incomplete",
    )
    _need(
        all(m["role"] in roles and m["member"] in roles for m in facts["memberships"]),
        "Origin capability membership inventory incomplete",
    )
    _need(
        all(
            isinstance(r["member_roles"], list)
            and r["oid"] in r["member_roles"]
            and set(r["member_roles"]) <= roles.keys()
            for r in roles.values()
        ),
        "Origin capability effective MEMBER inventory incomplete",
    )
    # Actual PG MEMBER includes implicit pg_database_owner membership, not just
    # physical pg_auth_members edges. NOINHERIT still permits explicit SET ROLE.
    effective = [
        {"member": r["oid"], "role": oid} for r in roles.values() for oid in r["member_roles"]
    ]
    owner = db["owner"]
    # MEMBER/SET ROLE closure intentionally ignores INHERIT. Private CONNECT
    # must also hold through every role a foreign login could SET ROLE to.
    for role in roles.values():
        reachable = _closure(role["oid"], effective)
        if role["login"] is not True or role["superuser"] is True or owner in reachable:
            continue
        _need(
            not any(roles[oid]["connect"] or roles[oid]["temp"] for oid in reachable),
            "Origin capability has a foreign connectable principal",
        )
    _need(roles[db["actor"]]["login"] is True, "Origin capability requires the bound login owner")
    reachable = _closure(db["actor"], effective)
    # Every non-super login able to become this owner is in the ordinary-writer
    # domain too. It must not offer an alternative replica/definer escape path.
    for role in roles.values():
        members = _closure(role["oid"], effective)
        if role["login"] is True and role["superuser"] is False and owner in members:
            reachable |= members
    while True:
        for oid in reachable:
            role = roles[oid]
            _need(
                not any(role[key] for key in _FLAGS)
                and role["parameter_set"] is False
                and role["parameter_alter_system"] is False
                and role["name"] not in _DANGEROUS,
                "Origin capability has a reachable privileged or replica-setting role",
            )
        grown = set(reachable)
        for function in facts["functions"]:
            # Existing DML trigger invocation does not recheck function EXECUTE.
            if not function["trigger_invoked"] and not reachable.intersection(
                function["execute_roles"]
            ):
                continue
            _need(
                function["sensitive_config"] is False,
                "Origin capability has a callable role or replication GUC override",
            )
            if function["definer"] is True:
                _need(function["owner"] in roles, "Origin definer owner inventory incomplete")
                grown |= _closure(function["owner"], effective)
        if grown == reachable:
            break
        reachable = grown
    # Dynamic tables/chunks/control objects use the same owner/ACL invariant;
    # their independent catalog pins remain the restore fence's responsibility.
    _need(
        facts["scope_acl_safe"] is True,
        "Origin capability has an unsafe table or column writer ACL",
    )
    authority = {
        key: facts[key]
        for key in (
            "database",
            "roles",
            "memberships",
            "parameter_acl",
            "functions",
            "extensions",
            "events",
        )
    }
    authority.update(
        format=FORMAT,
        mode=MODE,
        other_clients=False,
        subscriptions=False,
        scheduled_jobs=False,
    )
    return {"sha256": digest(authority), "authority": authority}


def capture(db):
    """Read only fixed catalog metadata; never return SQL bodies or credentials."""
    _need(db.dialect.name == "postgresql", "Origin capability requires PostgreSQL")
    try:
        database = dict(
            db.execute(
                text("""SELECT d.oid::bigint AS oid,d.datdba::bigint AS owner,
          (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user) AS actor,
          (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=session_user) AS session,
          pg_catalog.current_setting('server_version_num')::int AS version,pg_catalog.pg_is_in_recovery() AS recovery,
          pg_catalog.current_setting('session_replication_role') AS origin,d.datacl::text AS acl,
          pg_catalog.has_database_privilege('public',d.oid,'CONNECT') AS public_connect,
          pg_catalog.has_database_privilege('public',d.oid,'TEMPORARY') AS public_temp
          FROM pg_catalog.pg_database d WHERE d.datname=pg_catalog.current_database()""")
            )
            .mappings()
            .one()
        )
        roles = [
            dict(r)
            for r in db.execute(
                text("""SELECT r.oid::bigint AS oid,r.rolname AS name,
          r.rolcanlogin AS login,r.rolsuper AS superuser,r.rolinherit AS inherit,
          r.rolcreaterole AS create_role,r.rolcreatedb AS create_db,
          r.rolreplication AS replication,r.rolbypassrls AS bypass_rls,
          pg_catalog.has_parameter_privilege(r.oid,'session_replication_role','SET') AS parameter_set,
          pg_catalog.has_parameter_privilege(r.oid,'session_replication_role','ALTER SYSTEM') AS parameter_alter_system,
          ARRAY(SELECT rr.oid::bigint FROM (SELECT oid FROM pg_catalog.pg_roles
            ORDER BY oid LIMIT 129) rr WHERE pg_catalog.pg_has_role(r.oid,rr.oid,'MEMBER')
            ORDER BY rr.oid) AS member_roles,
          pg_catalog.has_database_privilege(r.oid,pg_catalog.current_database(),'CONNECT') AS connect,
          pg_catalog.has_database_privilege(r.oid,pg_catalog.current_database(),'TEMPORARY') AS temp
          FROM pg_catalog.pg_roles r ORDER BY r.oid LIMIT 129""")
            ).mappings()
        ]
        _need(len(roles) <= 128, "Origin capability role budget exceeded")
        memberships = [
            dict(r)
            for r in db.execute(
                text("""SELECT roleid::bigint AS role,
          member::bigint AS member,grantor::bigint AS grantor,admin_option
          FROM pg_catalog.pg_auth_members ORDER BY roleid,member LIMIT 257""")
            ).mappings()
        ]
        _need(len(memberships) <= 256, "Origin capability membership budget exceeded")
        parameter_acl = [
            dict(r)
            for r in db.execute(
                text("""SELECT oid::bigint AS oid,
          parname,paracl::text AS acl FROM pg_catalog.pg_parameter_acl
          WHERE parname='session_replication_role' ORDER BY oid""")
            ).mappings()
        ]
        raw_functions = (
            db.execute(
                text("""SELECT p.oid::bigint AS oid,p.proowner::bigint AS owner,
          p.prosecdef AS definer,p.proconfig AS config,
          pg_catalog.to_jsonb(p)-'prosrc'-'proconfig' AS catalog,
          CASE WHEN pg_catalog.octet_length(p.prosrc)<=65536 THEN p.prosrc ELSE NULL END AS source,
          EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE t.tgfoid=p.oid AND n.nspname NOT IN ('pg_catalog','information_schema')) AS trigger_invoked,
          ARRAY(SELECT r.oid::bigint FROM pg_catalog.pg_roles r
            WHERE pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE') ORDER BY r.oid) AS execute_roles
          FROM pg_catalog.pg_proc p WHERE p.prosecdef OR EXISTS (
            SELECT 1 FROM pg_catalog.unnest(p.proconfig) c WHERE pg_catalog.lower(pg_catalog.split_part(c,'=',1))
            IN ('session_replication_role','role','session_authorization'))
          ORDER BY p.oid LIMIT 129""")
            )
            .mappings()
            .all()
        )
        _need(
            len(raw_functions) <= 128 and all(r["source"] is not None for r in raw_functions),
            "Origin capability function inventory exceeds budget",
        )
        functions = [
            {
                "oid": r["oid"],
                "owner": r["owner"],
                "definer": r["definer"],
                "sensitive_config": any(
                    c.split("=", 1)[0].strip().lower() in _CONFIG for c in r["config"] or []
                ),
                "trigger_invoked": r["trigger_invoked"],
                "execute_roles": list(r["execute_roles"]),
                "catalog_sha256": digest(dict(r)),
            }
            for r in raw_functions
        ]
        subscriptions = db.execute(
            text("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_subscription
          WHERE subdbid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()))""")
        ).scalar_one()
        other_clients = db.execute(
            text("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity
          WHERE datid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database())
          AND pid<>pg_catalog.pg_backend_pid() AND backend_type='client backend')""")
        ).scalar_one()
        jobs_view = db.execute(
            text("SELECT pg_catalog.to_regclass('timescaledb_information.jobs') IS NOT NULL")
        ).scalar_one()
        scheduled = (
            db.execute(
                text("SELECT EXISTS(SELECT 1 FROM timescaledb_information.jobs WHERE scheduled)")
            ).scalar_one()
            if jobs_view
            else False
        )
        events = _events(db)
        scope_acl_safe = _scope_acl(db, database["owner"])
        extensions = [
            dict(r)
            for r in db.execute(
                text("""SELECT oid::bigint AS oid,
          extname AS name,extowner::bigint AS owner,extnamespace::bigint AS namespace,
          extversion AS version FROM pg_catalog.pg_extension ORDER BY oid LIMIT 33""")
            ).mappings()
        ]
        return validate(
            {
                "database": database,
                "roles": roles,
                "memberships": memberships,
                "parameter_acl": parameter_acl,
                "functions": functions,
                "subscriptions": subscriptions,
                "other_clients": other_clients,
                "scheduled_jobs": scheduled,
                "events": events,
                "scope_acl_safe": scope_acl_safe,
                "extensions": extensions,
            }
        )
    except SQLAlchemyError:
        raise ArchiveBlocked("Origin capability catalog proof unavailable") from None


def _scope_acl(db, owner):
    # All public application/control relations plus physical inherited tables.
    # PUBLIC column INSERT/UPDATE grants count even without table-wide grants.
    rows = (
        db.execute(
            text("""WITH RECURSIVE protected(oid) AS (
      SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p')
      UNION SELECT i.inhrelid FROM pg_catalog.pg_inherits i JOIN protected p ON i.inhparent=p.oid)
      SELECT c.oid::bigint AS oid,c.relowner::bigint AS owner,
        pg_catalog.has_table_privilege('public',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') AS public_write,
        pg_catalog.has_any_column_privilege('public',c.oid,'INSERT,UPDATE') AS public_column_write
      FROM protected p JOIN pg_catalog.pg_class c ON c.oid=p.oid LIMIT 513""")
        )
        .mappings()
        .all()
    )
    return len(rows) <= 512 and all(
        r["owner"] == owner and r["public_write"] is False and r["public_column_write"] is False
        for r in rows
    )


def _events(db):
    """Only the two reviewed TS2.17.2 provider hooks, never arbitrary event DDL."""
    return [
        dict(r)
        for r in db.execute(
            text("""SELECT e.oid::bigint AS oid,
      e.evtname AS name,e.evtevent AS event,e.evtowner::bigint AS owner,
      e.evtenabled AS enabled,e.evttags AS tags,e.evtfoid::bigint AS function_oid,
      (SELECT d.refobjid::bigint FROM pg_catalog.pg_depend d
        WHERE d.classid='pg_catalog.pg_event_trigger'::pg_catalog.regclass AND d.objid=e.oid
        AND d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass AND d.deptype='e') AS extension_oid,
      p.proname AS function_name,n.nspname AS function_schema,p.proowner::bigint AS function_owner,
      l.lanname AS language,p.probin AS binary,p.prosrc AS symbol,
      p.pronargs AS args,p.prosecdef AS definer,p.proconfig AS config,p.proacl::text AS acl,
      (p.provariadic=0 AND p.pronargdefaults=0 AND p.proallargtypes IS NULL
        AND p.proargmodes IS NULL AND p.proargnames IS NULL) AS plain_signature,
      p.prorettype='pg_catalog.event_trigger'::pg_catalog.regtype AS returns_event_trigger,
      p.proretset AS returns_set,p.proisstrict AS strict,p.proleakproof AS leakproof,
      p.provolatile AS volatility,p.proparallel AS parallel,p.prokind AS kind,
      (SELECT d.refobjid::bigint FROM pg_catalog.pg_depend d
        WHERE d.classid='pg_catalog.pg_proc'::pg_catalog.regclass AND d.objid=p.oid
        AND d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass AND d.deptype='e') AS function_extension_oid,
      pg_catalog.to_jsonb(e) AS full_event,pg_catalog.to_jsonb(p) AS full_function
      FROM pg_catalog.pg_event_trigger e JOIN pg_catalog.pg_proc p ON p.oid=e.evtfoid
      JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_catalog.pg_language l ON l.oid=p.prolang ORDER BY e.oid LIMIT 4""")
        ).mappings()
    ]


def _events_valid(events, extensions, roles):
    provider = [e for e in extensions if e["name"] == "timescaledb" and e["version"] == "2.17.2"]
    if len(provider) != 1 or len(events) != 2 or roles.get(10, {}).get("superuser") is not True:
        return False
    if {e["name"] for e in events} != {
        "timescaledb_ddl_command_end",
        "timescaledb_ddl_sql_drop",
    } or len({e["function_oid"] for e in events}) != 1:
        return False
    for event in events:
        tags = event["tags"]
        definition = (
            (
                event["event"] == "ddl_command_end"
                and tags is not None
                and len(tags) == 8
                and set(tags) == _DDL_TAGS
            )
            if event["name"] == "timescaledb_ddl_command_end"
            else (event["event"] == "sql_drop" and tags is None)
        )
        if not (
            definition
            and event["owner"] == event["function_owner"] == 10
            and event["enabled"] == "O"
            and event["extension_oid"] == event["function_extension_oid"] == provider[0]["oid"]
            and event["function_name"] == "process_ddl_event"
            and event["function_schema"] == "_timescaledb_functions"
            and event["language"] == "c"
            and event["binary"] == "$libdir/timescaledb-2.17.2"
            and event["symbol"] == "ts_timescaledb_process_ddl_event"
            and event["args"] == 0
            and event["plain_signature"] is True
            and event["definer"] is False
            and event["config"] is None
            and event["acl"] is None
            and event["returns_event_trigger"] is True
            and event["returns_set"] is False
            and event["strict"] is False
            and event["leakproof"] is False
            and event["volatility"] == "v"
            and event["parallel"] == "u"
            and event["kind"] == "f"
        ):
            return False
    return True


def verify(db, expected_sha256):
    proof = capture(db)
    _need(proof["sha256"] == expected_sha256, "Origin capability authority changed")
    return proof
