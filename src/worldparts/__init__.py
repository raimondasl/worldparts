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

from worldparts.catalog import Catalog, default_catalog, load_catalog
from worldparts.contracts import (
    CheckOutcome,
    ComponentReport,
    check_catalog,
    run_component,
    run_contract,
    run_scenario,
)
from worldparts.errors import (
    ContractError,
    ExpressionError,
    IncompatiblePortsError,
    InvalidValueError,
    ManifestError,
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
from worldparts.results import (
    ComponentWarning,
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
    "Catalog",
    "CheckOutcome",
    "ComponentReport",
    "ComponentWarning",
    "ContractError",
    "ExpressionError",
    "IncompatiblePortsError",
    "InvalidValueError",
    "Issue",
    "Manifest",
    "ManifestError",
    "ModeChange",
    "OutOfRangeError",
    "SelfConnectionError",
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
    "check_catalog",
    "convert",
    "default_catalog",
    "load_catalog",
    "load_manifest",
    "parse_duration",
    "parse_value",
    "run_component",
    "run_contract",
    "run_scenario",
    "validate_manifest_data",
]
