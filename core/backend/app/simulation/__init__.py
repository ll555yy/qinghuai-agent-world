"""Opt-in seven-day simulation helpers.

The simulation package is deliberately separate from the HTTP application.  It
drives the same :class:`WorldEngine` used by the API and is safe to import in
offline tests.
"""

from .manifest import (
    ATTEMPT_ID_RE,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_MANIFEST_SCHEMA_PATH,
    DEFAULT_MANIFEST_SHA256_PATH,
    DEFAULT_PREREG_SEEDS,
    EXPECTED_MANIFEST_ROUTES,
    AttemptLedger,
    AttemptRecord,
    AttemptStatus,
    ManifestValidationError,
    canonical_manifest_sha256,
    load_manifest,
    make_attempt_id,
    planned_attempts,
    validate_manifest,
)
from .runner import (
    DEFAULT_SIMULATION_SEED,
    SevenDaySimulationRunner,
    SimulationBudget,
    SimulationMetrics,
    SimulationMode,
    SimulationReport,
    SimulationRoute,
)

__all__ = [
    "DEFAULT_SIMULATION_SEED",
    "SimulationBudget",
    "SimulationMetrics",
    "SimulationMode",
    "SimulationReport",
    "SimulationRoute",
    "SevenDaySimulationRunner",
    "ATTEMPT_ID_RE",
    "AttemptLedger",
    "AttemptRecord",
    "AttemptStatus",
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_MANIFEST_SCHEMA_PATH",
    "DEFAULT_MANIFEST_SHA256_PATH",
    "DEFAULT_PREREG_SEEDS",
    "EXPECTED_MANIFEST_ROUTES",
    "ManifestValidationError",
    "canonical_manifest_sha256",
    "load_manifest",
    "make_attempt_id",
    "planned_attempts",
    "validate_manifest",
]
