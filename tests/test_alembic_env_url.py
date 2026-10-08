"""The database URL survives alembic's ConfigParser, and never leaks through it.

The first migrate task against RDS died before connecting: `Config.set_main_option`
stores values in a ConfigParser, which reads `%` as interpolation syntax, and a
URL-encoded password is full of `%`. The ValueError carried the whole URL --
password included -- into the task's log. Two things are pinned here: the escape
that makes the value storable, and that env.py applies it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from alembic.config import Config

ENV = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
URL = "postgresql+psycopg2://edge_owner:8%5BiB%24E%21@db.example.test:5432/edge?sslmode=require"


def test_a_percent_encoded_url_is_refused_unescaped():
    # The premise: the failure is real, in this alembic.
    with pytest.raises(ValueError, match="interpolation"):
        Config().set_main_option("sqlalchemy.url", URL)


def test_the_double_percent_escape_round_trips():
    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", URL.replace("%", "%%"))
    assert cfg.get_main_option("sqlalchemy.url") == URL


def _set_main_option_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set_main_option"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "sqlalchemy.url"
        ):
            yield node


def test_env_py_escapes_the_url_before_storing_it():
    tree = ast.parse(ENV.read_text(encoding="utf-8"))
    calls = list(_set_main_option_calls(tree))
    assert len(calls) == 1, "env.py stores sqlalchemy.url in exactly one place"
    value = calls[0].args[1]
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute), ast.dump(value)
    assert value.func.attr == "replace"
    assert [a.value for a in value.args if isinstance(a, ast.Constant)] == ["%", "%%"]


def test_env_py_does_not_let_the_value_into_an_exception():
    # The store is wrapped so a ValueError from ConfigParser -- whose message
    # quotes the value -- is replaced, not propagated.
    tree = ast.parse(ENV.read_text(encoding="utf-8"))
    (call,) = _set_main_option_calls(tree)
    wrapped = any(
        isinstance(node, ast.Try) and any(call is c for b in node.body for c in ast.walk(b))
        for node in ast.walk(tree)
    )
    assert wrapped, "set_main_option is not inside a try"
