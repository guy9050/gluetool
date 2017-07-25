import pytest

import psycopg2
import libci
import libci.modules.infrastructure.postgresql
from mock import MagicMock
from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.infrastructure.postgresql.CIPostgreSQL)


@pytest.fixture(name='configured_module')
def fixture_configured_module(module, monkeypatch):
    ci, module = module

    def options_mock(key):
        return {
            "user": "user1",
            "password": "password1",
            "host": "host1",
            "port": "1234",
            "dbname": "dbname1"
        }[key]
    monkeypatch.setattr(module, "option", options_mock)
    return ci, module


def test_loadable(module):
    # pylint: disable=protected-access
    ci, _ = module
    python_mod = ci._load_python_module("infrastructure/postgresql", "pytest_postgresql",
                                        "libci/modules/infrastructure/postgresql.py")
    assert hasattr(python_mod, "CIPostgreSQL")


def test_shared(module):
    ci, module = module
    assert ci.has_shared("postgresql")
    assert ci.has_shared("postgresql_cursor")


def test_shared_postgresql(module):
    ci, _ = module
    assert ci.shared("postgresql") is None


def test_shared_postgresql_reconnect(module, monkeypatch):
    ci, _ = module
    connection_mock = MagicMock()
    monkeypatch.setattr(psycopg2, "connect", MagicMock(return_value=connection_mock))
    assert ci.shared("postgresql", reconnect=True) == connection_mock


def test_shared_postgresql_cursor_fail(module):
    ci, _ = module
    with pytest.raises(libci.CIError, match=r"connection object not initialized"):
        ci.shared("postgresql_cursor")


def test_shared_postgresql_cursor_reconnect(module, monkeypatch):
    ci, _ = module
    cursor_mock = MagicMock()
    connection_mock = MagicMock(cursor=cursor_mock)
    monkeypatch.setattr(psycopg2, "connect", MagicMock(return_value=connection_mock))
    assert ci.shared("postgresql_cursor", reconnect=True) == cursor_mock()


def test_connect(configured_module, monkeypatch):
    # pylint: disable=protected-access
    _, module = configured_module
    connection_mock = MagicMock()
    connect_mock = MagicMock(return_value=connection_mock)
    monkeypatch.setattr(psycopg2, "connect", connect_mock)
    assert module._connection is None
    module.connect()
    connect_mock.assert_called_with(host="host1", port="1234", dbname="dbname1", user="user1", password="password1")
    assert module._connection is connection_mock


def test_connect_fail(module, monkeypatch):
    _, module = module
    monkeypatch.setattr(psycopg2, "connect", MagicMock(side_effect=Exception))
    with pytest.raises(libci.CIError, match=r"could not connect to PostgreSQL"):
        module.connect()


def test_execute(configured_module, monkeypatch, log):
    _, module = configured_module
    cursor_mock = MagicMock(fetchone=MagicMock(return_value=["TEEID 1.2"]))
    connection_mock = MagicMock(cursor=MagicMock(return_value=cursor_mock))
    monkeypatch.setattr(psycopg2, "connect", MagicMock(return_value=connection_mock))
    module.execute()
    message = "connected to postgresql 'host1' version 'TEEID 1.2'"
    assert any(record.message == message for record in log.records)


def test_execute_fail_server_version(configured_module, monkeypatch):
    _, module = configured_module
    cursor_mock = MagicMock(fetchone=MagicMock(return_value=None))
    connection_mock = MagicMock(cursor=MagicMock(return_value=cursor_mock))
    monkeypatch.setattr(psycopg2, "connect", MagicMock(return_value=connection_mock))
    with pytest.raises(libci.CIError, match=r"could not fetch server version"):
        module.execute()
