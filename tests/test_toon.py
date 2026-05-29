"""Tests for the TOON encoder/decoder."""

import pytest

from hive.cli import toon


# ── encoding ─────────────────────────────────────────────────────────────────
def test_encode_scalars():
    out = toon.encode({"name": "hive", "count": 3, "active": True, "missing": None})
    assert "name: hive" in out
    assert "count: 3" in out
    assert "active: true" in out
    assert "missing:" in out  # None renders as an empty value


def test_encode_nested_dict():
    out = toon.encode({"daemon": {"running": True, "pid": 4821}})
    assert "daemon:" in out
    assert "  running: true" in out
    assert "  pid: 4821" in out


def test_encode_uniform_array_is_tabular():
    out = toon.encode({"issues": [{"id": "a", "n": 1}, {"id": "b", "n": 2}]})
    assert "issues[2]{id,n}:" in out
    assert "  a,1" in out
    assert "  b,2" in out


def test_encode_scalar_array_inline():
    out = toon.encode({"tags": ["bug", "p1"]})
    assert "tags[2]: bug,p1" in out


def test_encode_empty_array_is_explicit():
    out = toon.encode({"issues": []})
    assert "issues[0]:" in out


def test_encode_object_array_flattens_nonscalar_cells():
    # Object arrays stay tabular even when a cell is a list/dict: scalar lists join
    # with `|`, empty containers become a blank cell, dicts become compact JSON.
    data = {
        "issues": [
            {"id": "a", "tags": ["bug", "p1"], "meta": {"k": 1}},
            {"id": "b", "tags": [], "meta": None},
        ]
    }
    out = toon.encode(data)
    assert "issues[2]{id,tags,meta}:" in out  # stays tabular, no expanded "- " form
    assert "- " not in out

    rows = toon.decode(out)["issues"]
    assert rows[0]["tags"] == "bug|p1"  # scalar list joined with |
    assert rows[0]["meta"] == '{"k":1}'  # dict flattened to compact JSON string
    assert rows[1]["tags"] is None and rows[1]["meta"] is None  # empty list + None -> blank


def test_encode_nonuniform_array_raises():
    with pytest.raises(ValueError):
        toon.encode({"rows": [{"a": 1}, {"a": 1, "b": 2}]})


def test_encode_quotes_commas_and_newlines():
    out = toon.encode({"rows": [{"t": "a, b"}, {"t": "line1\nline2"}]})
    assert '"a, b"' in out
    assert "\\n" in out  # newline escaped inside the quoted cell


def test_encode_help_block():
    out = toon.encode_help(["hive show <id>", "hive list --todo"])
    assert out == "help[2]:\n  hive show <id>\n  hive list --todo"


def test_toon_error_contract():
    assert toon.toon_error("Issue not found: x") == "error: Issue not found: x"


# ── round-trip ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "data",
    [
        {"count": 2, "total": 12, "issues": [{"id": "a", "title": "x, y", "p": 1}, {"id": "b", "title": "z", "p": 2}]},
        {"project": "hive", "issues": {"open": 3, "done": 7}, "daemon": {"running": True, "pid": 1}, "ready": []},
        {"count": 0, "issues": [], "note": "none"},
        {"tags": ["bug", "P1"], "depends_on": []},
    ],
)
def test_round_trip(data):
    assert toon.decode(toon.encode(data)) == data
