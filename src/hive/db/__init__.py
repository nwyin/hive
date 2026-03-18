from .core import DatabaseCore, SCHEMA, normalize_tags
from .issues import IssuesMixin
from .notes import NotesMixin
from .metrics import MetricsMixin


class Database(IssuesMixin, NotesMixin, MetricsMixin, DatabaseCore):
    pass


__all__ = ["Database", "normalize_tags", "SCHEMA"]
