"""TOON (Token-Optimized Object Notation) encoder for hive's machine output.

TOON is hive's single machine-facing serialization format. It drops the braces,
quotes, and commas that make JSON token-heavy while staying unambiguous for an LLM
to read. The grammar we emit is a small, well-defined subset:

* scalars            ``key: value``                  (None -> empty, bools -> true/false)
* nested objects     ``key:`` then 2-space-indented child lines
* object arrays      ``key[N]{f1,f2,f3}:`` then one indented comma-joined row per item
                     (every item must share the same key set; a non-scalar cell is
                     flattened into the cell — scalar lists join with ``|``, anything
                     else becomes compact JSON)
* scalar arrays      ``key[N]: a,b,c``  (inline) or one value per indented line (block)
* empty arrays       ``key[0]:``                       (the explicit empty-state signal)

There is intentionally no nested-array ("expanded list") form: hive's command
results never contain dicts-inside-arrays, so the encoder keeps object arrays flat
and tabular. An array shape it can't render flatly (e.g. rows with differing keys)
raises rather than silently degrading — that fails loud if a future command emits an
unsupported shape.

``decode`` is the inverse, used by tests to assert on structure; encode/decode
round-trip for the shapes hive actually emits (a flattened non-scalar cell decodes
back to its string form, not the original container).

This module is intentionally dependency-free and hand-rolled — the surface is small
and we avoid pinning an immature third-party TOON package.
"""

import json
import re

__all__ = ["encode", "encode_help", "decode", "toon_error", "COMMAND_HINTS"]

_INDENT = "  "
_INLINE_ARRAY_MAX = 80


# ── next-step hints (AXI principle 9) ───────────────────────────────────────────
# Concrete command templates appended as a ``help[]`` block in machine mode. Fixed
# disambiguating flags are carried forward; runtime values stay parameterized as
# ``<placeholders>`` rather than guessed.
COMMAND_HINTS: dict[str, list[str]] = {
    "create": ["hive show <id>", "hive list --todo"],
    "list_issues": ["hive show <id>", 'hive create "<title>" --priority <0-4>'],
    "show": ["hive update <id> --status <status>", 'hive finalize <id> --resolution "<text>"'],
    "update": ["hive show <id>"],
    "cancel": ["hive list --todo"],
    "finalize": ["hive review", "hive list --todo"],
    "retry": ["hive show <id>", "hive status"],
    "review": ['hive finalize <id> --resolution "<text>"', "hive show <id>"],
    "status": ["hive review", "hive logs -n 50"],
    "list_agents": ["hive agents <id>", "hive status"],
    "merges": ["hive status", "hive logs --type merge"],
    "metrics": ["hive metrics --costs", "hive metrics --group-by tag"],
    "logs": ["hive logs -f", "hive show <id>"],
    "add_note": ['hive create "<title>"'],
    "dep_add": ["hive show <id>", "hive dep remove <id> <depends_on>"],
    "dep_remove": ["hive show <id>"],
}


# ── scalar formatting ───────────────────────────────────────────────────────────
def _fmt_scalar(value, *, in_row: bool = False) -> str:
    """Render a scalar. ``in_row`` quotes commas too (rows are comma-delimited)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    needs_quote = s == "" or s != s.strip() or '"' in s or "\n" in s
    if in_row:
        needs_quote = needs_quote or ("," in s)
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return s


def _is_scalar(value) -> bool:
    return not isinstance(value, (dict, list))


def _uniform_keys(arr: list) -> list | None:
    """If ``arr`` is a non-empty list of dicts sharing one key set, return the key
    order from the first item; otherwise None. Cell values need not be scalar —
    non-scalar cells are flattened by ``_cell``."""
    if not arr or not all(isinstance(x, dict) for x in arr):
        return None
    keys = list(arr[0].keys())
    keyset = set(keys)
    for item in arr:
        if set(item.keys()) != keyset:
            return None
    return keys


def _cell(value):
    """Flatten a cell value to a scalar for tabular rendering.

    Scalars pass through; a list of scalars joins with ``|``; anything else
    (dict, nested list) becomes compact JSON. The result is then quoted as needed
    by ``_fmt_scalar``.
    """
    if _is_scalar(value):
        return value
    if not value:  # empty list/dict -> blank cell
        return None
    if isinstance(value, list) and all(_is_scalar(x) for x in value):
        return "|".join("" if x is None else str(x) for x in value)
    return json.dumps(value, separators=(",", ":"), default=str)


# ── encoding ────────────────────────────────────────────────────────────────────
def encode(data) -> str:
    """Encode a JSON-serialisable dict/list/scalar to TOON text."""
    lines: list[str] = []
    if isinstance(data, dict):
        _encode_dict(data, 0, lines)
    elif isinstance(data, list):
        _encode_array("items", data, 0, lines)
    else:
        lines.append(_fmt_scalar(data))
    return "\n".join(lines)


def _encode_dict(d: dict, indent: int, lines: list[str]) -> None:
    pad = _INDENT * indent
    for key, value in d.items():
        if isinstance(value, dict):
            if value:
                lines.append(f"{pad}{key}:")
                _encode_dict(value, indent + 1, lines)
            else:
                lines.append(f"{pad}{key}:")
        elif isinstance(value, list):
            _encode_array(key, value, indent, lines)
        else:
            lines.append(f"{pad}{key}: {_fmt_scalar(value)}".rstrip())


def _encode_array(key: str, arr: list, indent: int, lines: list[str]) -> None:
    pad = _INDENT * indent
    child = _INDENT * (indent + 1)
    n = len(arr)

    if n == 0:
        lines.append(f"{pad}{key}[0]:")
        return

    # All scalars -> inline when short and comma-free, else block (one per line).
    if all(_is_scalar(x) for x in arr):
        formatted = [_fmt_scalar(x, in_row=True) for x in arr]
        inline = ",".join(formatted)
        simple = all("," not in str(x) and "\n" not in str(x) for x in arr if x is not None)
        if simple and len(inline) <= _INLINE_ARRAY_MAX:
            lines.append(f"{pad}{key}[{n}]: {inline}")
        else:
            lines.append(f"{pad}{key}[{n}]:")
            for f in formatted:
                lines.append(f"{child}{f}")
        return

    # Object array -> tabular (uniform keys required; non-scalar cells flattened).
    keys = _uniform_keys(arr)
    if keys is not None:
        header = ",".join(keys)
        lines.append(f"{pad}{key}[{n}]{{{header}}}:")
        for item in arr:
            row = ",".join(_fmt_scalar(_cell(item[k]), in_row=True) for k in keys)
            lines.append(f"{child}{row}")
        return

    # Anything else (rows with differing keys, mixed scalars/dicts) is a shape hive
    # is not expected to emit. Fail loud rather than silently degrade.
    raise ValueError(f"TOON encode: unsupported array shape for key {key!r} — expected scalars or uniform-key dicts")


def encode_help(hints: list[str]) -> str:
    """Render a ``help[]`` block of next-step command templates, one per line."""
    lines = [f"help[{len(hints)}]:"]
    for hint in hints:
        lines.append(f"{_INDENT}{hint}")
    return "\n".join(lines)


def toon_error(msg: str) -> str:
    """The structured error contract: ``error: <msg>`` (exit code 1 set by caller)."""
    return f"error: {_fmt_scalar(msg)}"


# ── decoding (for tests / round-trip) ────────────────────────────────────────────
_KEY_LINE = re.compile(r"^([^:\[{]+)(?:\[(\d+)\])?(?:\{([^}]*)\})?:\s?(.*)$")


def decode(text: str):
    """Parse TOON text back into a dict (best-effort inverse of ``encode``)."""
    raw = [ln for ln in text.split("\n") if ln.strip() != ""]
    lines = [((len(ln) - len(ln.lstrip(" "))) // 2, ln.strip()) for ln in raw]
    pos = [0]
    return _parse_block(lines, pos, 0)


def _parse_block(lines, pos, indent):
    result = {}
    while pos[0] < len(lines):
        ind, text = lines[pos[0]]
        if ind != indent:
            break
        pos[0] += 1
        m = _KEY_LINE.match(text)
        if not m:
            continue
        key, n_str, fields_str, rest = m.group(1), m.group(2), m.group(3), m.group(4)

        if n_str is not None:  # an array of some kind
            n = int(n_str)
            if fields_str is not None:  # tabular
                keys = fields_str.split(",") if fields_str else []
                result[key] = _read_rows(lines, pos, n, keys, indent + 1)
            elif rest != "":  # inline scalar array
                result[key] = _split_row(rest)
            elif n == 0:
                result[key] = []
            else:  # block scalar array
                vals = []
                for _ in range(n):
                    if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                        vals.append(_unquote(lines[pos[0]][1]))
                        pos[0] += 1
                result[key] = vals
        elif rest != "":  # plain scalar
            result[key] = _unquote(rest)
        elif pos[0] < len(lines) and lines[pos[0]][0] > indent:  # nested dict
            result[key] = _parse_block(lines, pos, indent + 1)
        else:  # bare "key:" with no children -> None
            result[key] = None
    return result


def _read_rows(lines, pos, n, keys, indent):
    rows = []
    for _ in range(n):
        if pos[0] >= len(lines) or lines[pos[0]][0] != indent:
            break
        vals = _split_row(lines[pos[0]][1])
        pos[0] += 1
        rows.append(dict(zip(keys, vals)))
    return rows


def _split_row(s: str) -> list:
    """Split a comma-delimited row, respecting double-quoted fields."""
    out, buf = [], []
    in_q = esc = False
    for c in s:
        buf.append(c)
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_q = not in_q
            continue
        if c == "," and not in_q:
            buf.pop()
            out.append("".join(buf))
            buf = []
    out.append("".join(buf))
    return [_unquote(x) for x in out]


_INT_RE = re.compile(r"-?\d+$")
_FLOAT_RE = re.compile(r"-?\d+\.\d+$")


def _unquote(s: str):
    """Inverse of ``_fmt_scalar`` for a single field."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner, out, i = s[1:-1], [], 0
        while i < len(inner):
            c = inner[i]
            if c == "\\" and i + 1 < len(inner):
                nxt = inner[i + 1]
                out.append({"n": "\n", '"': '"', "\\": "\\"}.get(nxt, nxt))
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out)
    if s == "":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s
