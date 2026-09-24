"""worldparts: agent-ready physical component models.

Quick start::

    import worldparts as wp

    s = wp.System("demo")
    s.add("mains", "supply", pressure="3 bar")
    s.add("v", "valve", kv=2.5, opening=0.5)
    s.add("out", "drain")
    s.connect("mains.port", "v.port_a")
    s.connect("v.port_b", "out.port")
    r = s.solve()
    print(r["v.volume_flow"], r.units["v.volume_flow"])
"""

from worldparts.calibration import (
    CalibrationResult,
    IdentifiabilityReport,
    ParameterEstimate,
    PathFit,
    Residual,
    SensorCandidate,
    calibrate,
    identifiability,
)
from worldparts.catalog import Catalog, default_catalog, load_catalog
from worldparts.contracts import (
    CheckOutcome,
    ComponentReport,
    check_catalog,
    run_component,
    run_contract,
    run_scenario,
)
from worldparts.controls import Control, ControlConflictError, InvalidControlError
from worldparts.diagnosis import (
    DiagnosisResult,
    Discriminator,
    FaultEstimate,
    Hypothesis,
    SensorSuggestion,
    diagnose,
)
from worldparts.errors import (
    CalibrationError,
    ContractError,
    ExpressionError,
    IncompatiblePortsError,
    InvalidValueError,
    ManifestError,
    MeasurementError,
    OutOfRangeError,
    SelfConnectionError,
    SolverError,
    SystemCheckError,
    UnitError,
    UnknownComponentError,
    UnknownPortError,
    UnknownVariableError,
    WorldpartsError,
)
from worldparts.manifest import Manifest, Table, load_manifest, validate_manifest_data
from worldparts.measurements import (
    MeasuredValue,
    MeasurementPoint,
    MeasurementSet,
    default_sigma,
    load_measurements,
)
from worldparts.results import (
    ComponentWarning,
    ControlReport,
    Issue,
    ModeChange,
    SimulationResult,
    SolveResult,
    VariableInfo,
)
from worldparts.system import System
from worldparts.units import P_ATM, convert, parse_duration, parse_value

__version__ = "0.1.0"

__all__ = [
    "P_ATM",
    "CalibrationError",
    "CalibrationResult",
    "Catalog",
    "CheckOutcome",
    "ComponentReport",
    "ComponentWarning",
    "ContractError",
    "Control",
    "ControlConflictError",
    "ControlReport",
    "DiagnosisResult",
    "Discriminator",
    "ExpressionError",
    "FaultEstimate",
    "Hypothesis",
    "IdentifiabilityReport",
    "IncompatiblePortsError",
    "InvalidControlError",
    "InvalidValueError",
    "Issue",
    "Manifest",
    "ManifestError",
    "MeasuredValue",
    "MeasurementError",
    "MeasurementPoint",
    "MeasurementSet",
    "ModeChange",
    "OutOfRangeError",
    "ParameterEstimate",
    "PathFit",
    "Residual",
    "SelfConnectionError",
    "SensorCandidate",
    "SensorSuggestion",
    "SimulationResult",
    "SolveResult",
    "SolverError",
    "System",
    "SystemCheckError",
    "Table",
    "UnitError",
    "UnknownComponentError",
    "UnknownPortError",
    "UnknownVariableError",
    "VariableInfo",
    "WorldpartsError",
    "__version__",
    "calibrate",
    "check_catalog",
    "convert",
    "default_catalog",
    "default_sigma",
    "diagnose",
    "identifiability",
    "load_catalog",
    "load_manifest",
    "load_measurements",
    "parse_duration",
    "parse_value",
    "run_component",
    "run_contract",
    "run_scenario",
    "validate_manifest_data",
]
