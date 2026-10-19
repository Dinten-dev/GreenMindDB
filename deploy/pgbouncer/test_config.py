import importlib.util
from pathlib import Path
from urllib.parse import quote

import pytest

spec = importlib.util.spec_from_file_location('pg_config', Path(__file__).with_name('config.py'))
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)


def urls():
    return [f'postgresql+psycopg2://{r}:{quote("test-only" + r, safe="")}@127.0.0.1:5432/{d}' for r in pg.ROLES for d in pg.DATABASES]


def test_inventory_is_complete_and_bounded():
    assert set(pg.credentials(urls())) == pg.ROLES
    text = pg.config()
    assert 'pool_mode = transaction' in text
    assert 'reserve_pool_size = 0' in text
    assert text.count('max_db_connections=2') == 2
    assert '*' not in text and 'ignore_startup_parameters' not in text
    assert 'server_reset_query_always = 0' in text
    assert 'statement_timeout,lock_timeout,work_mem,jit,idle_in_transaction_session_timeout' in text


@pytest.mark.parametrize('replacement', ['127.0.0.2', 'localhost', '0.0.0.0'])
def test_external_or_ambiguous_hosts_rejected(replacement):
    with pytest.raises(ValueError):
        pg.credentials([x.replace('127.0.0.1', replacement) for x in urls()])


def test_missing_pair_and_admin_identity_rejected():
    with pytest.raises(ValueError):
        pg.credentials(urls()[:-1])
    with pytest.raises(ValueError):
        pg.credentials([x.replace(next(iter(pg.ROLES)), 'admin') for x in urls()])


def test_options_and_password_newline_rejected():
    with pytest.raises(ValueError):
        pg.credentials([x + '?options=unsafe' for x in urls()])
    first = urls()[0].replace('@127', '%0a@127')
    with pytest.raises(ValueError):
        pg.credentials([first] + urls()[1:])


def test_auth_escaping_and_no_admin_role():
    body = pg.auth_file({r: 'test-only-"\\' for r in pg.ROLES}, 'a' * 64)
    assert len(body.splitlines()) == 3
    assert '\\"' in body and '\\\\' in body
    assert 'admin_users' not in pg.config()
