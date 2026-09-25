"""Aggregates the feature task handlers so importing this module registers all
of them (used by the arq worker bootstrap)."""

# Each import pulls in a module that registers @task handlers as a side effect.
from ..modules.import_ import job as _import_job  # noqa: F401
