"""Sessions: what agents did, read from harness transcripts into a session index. See ``docs/concepts.md``.

Public entry point: ``Sessions``. Core selections are ``Session``, ``Thread``, and ``Trajectory``;
analysis projections and aggregates live in ``analysis``. Harness conversions live in ``export``;
behavioral matching lives in ``scanner``.
"""

from . import scanner
from .analysis import (
    DecisionTrace,
    WeightedActivity,
    WeightedPattern,
    WeightedPatternView,
    WeightedTransition,
    WorkGraph,
)
from .events import UnsupportedConversion
from .export import FORMATS, Export, export_events
from .index import SessionIndex
from .install import INSTALLABLE, InstalledSession, SessionAlreadyInstalled, install_export
from .markdown import evidence_to_markdown
from .models import (
    Event,
    IndexHit,
    Run,
    RunTrace,
    SearchFilters,
    SearchPage,
    SearchQuery,
    SessionMetadata,
    SourceConfig,
    SourceReference,
    SyncReport,
)
from .session import EventSequence, Results, SearchHit, Session, SessionView, Trajectory, TrajectorySplit
from .sessions import Sessions, default_index, detect_harness
from .threads import AgentThread, Thread, ThreadFamily, ThreadMatch
from .trajectories import split_trajectories

__all__ = [
    "FORMATS",
    "INSTALLABLE",
    "AgentThread",
    "DecisionTrace",
    "Event",
    "EventSequence",
    "Export",
    "IndexHit",
    "InstalledSession",
    "Results",
    "Run",
    "RunTrace",
    "SearchFilters",
    "SearchHit",
    "SearchPage",
    "SearchQuery",
    "Session",
    "SessionAlreadyInstalled",
    "SessionIndex",
    "SessionMetadata",
    "SessionView",
    "Sessions",
    "SourceConfig",
    "SourceReference",
    "SyncReport",
    "Thread",
    "ThreadFamily",
    "ThreadMatch",
    "Trajectory",
    "TrajectorySplit",
    "UnsupportedConversion",
    "WeightedActivity",
    "WeightedPattern",
    "WeightedPatternView",
    "WeightedTransition",
    "WorkGraph",
    "default_index",
    "detect_harness",
    "evidence_to_markdown",
    "export_events",
    "install_export",
    "scanner",
    "split_trajectories",
]
