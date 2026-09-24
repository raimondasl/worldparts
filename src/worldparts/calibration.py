"""Calibration and identifiability (design section 14.2).

:func:`calibrate` fits parameters or inputs of a :class:`~worldparts.System` to measured
operating points (:mod:`worldparts.measurements`) and reports how well each one is
determined. :func:`identifiability` answers the design question before any data exists:
which of the parameters a set of sensors can determine at given operating points, and which
extra sensor would help most.

Method, in short:

- **Residuals.** Each measured value ``y`` with standard uncertainty ``sigma`` gives the
  normalised residual ``(model - y) / sigma``. Steady points (no ``time``) apply their
  settings, then solve; timed points run one simulation from the current state that applies
  their settings as events at their times, and are compared with the simulated values at
  those times (linear interpolation between samples when a time falls between them).
  Before every model evaluation the system is put back to its starting values, so
  evaluations are independent and reproducible.
- **Time step.** Storage states are integrated with explicit Euler (design 5.5), whose error
  grows with the step and would be absorbed into the estimates unseen. By default the timed
  points are therefore predicted by Richardson extrapolation to zero step, ``2 P(h/2) -
  P(h)`` from simulations at steps ``h`` and ``h/2``; ``h`` starts at the median interval
  between measurement times (at most the document's ``simulation.step``) and is halved until
  halving it once more changes the predictions by at most :data:`STEP_TOLERANCE` (norm of
  the normalised residuals), at the starting values and again at the fitted values (refitting
  with the finer step when needed). A ``step`` given explicitly is used as ``System.simulate``
  uses it and is only checked; a failed check becomes a note.
- **Fit.** ``scipy.optimize.least_squares`` (``trf`` or ``dogbox``) minimises the sum of
  squared normalised residuals within the bounds, starting from the current values. It works
  in bound-range coordinates ``z = (x - lower) / (upper - lower)``, with each parameter's own
  scale (:func:`_scales`: its magnitude, at most its bound range, at least
  :data:`SCALE_FLOOR` of a range that includes zero) as the optimiser's variable scale. The
  Jacobian is by finite differences with a step of :data:`DIFF_STEP` of that scale, so a wide
  bound range (a manifest's hard limits, such as [1e-4, 1e5] for a Kv) does not coarsen it. A
  model failure at a trial point (the solver does not converge, a cross-parameter rule
  rejects the values) counts as an infinite residual, so the optimiser steps back; failures
  are counted and reported. A fit that ends worse than its start reports the start, with
  ``success`` False and a note.
- **Uncertainty.** At the optimum the Jacobian ``J`` of the normalised residuals is
  recomputed with Richardson-extrapolated finite differences (central where the bounds
  allow, one-sided otherwise). The same extrapolation from steps twice as large gives a
  second estimate with a larger error; their difference ``E`` bounds the numerical error of
  ``J`` (truncation and solver round-off). The covariance is ``s**2 (J^T J)^-1`` with ``s**2``
  the reduced chi-square when it exceeds 1 (otherwise 1), computed through the singular
  value decomposition of ``J`` scaled by each bound range. A singular direction ``v`` is
  null when its singular value is at or below :data:`NULL_RELATIVE` of the largest, the
  numerical error along it (``|| |E| |v| ||``, so a badly differenced parameter does not
  drag others into the null space), or :data:`NULL_ABSOLUTE` (moving across the whole bound
  ranges changes the normalised residuals by less than that). A parameter with a component
  above :data:`NULL_COMPONENT` in the null space is ``not_identifiable`` and has no standard
  error. Otherwise it is ``weak`` when its standard error exceeds :data:`WEAK_SE_FRACTION`
  of its bound range or its correlation with another parameter exceeds
  :data:`WEAK_CORRELATION` in magnitude, and ``identifiable`` if not.

Everything is advisory: the standard errors are local (linearised) and assume the model is
right; a fitted value pushed onto a bound, a reduced chi-square well above 1 or systematic
residuals say that it is not.
"""

from __future__ import annotations

import math
import os
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np
from scipy.optimize import least_squares
from scipy.stats import chi2 as chi2_distribution

from worldparts.errors import (
    CalibrationError,
    InvalidValueError,
    MeasurementError,
    SolverError,
    SystemCheckError,
    UnknownVariableError,
    WorldpartsError,
    format_choices,
)
from worldparts.measurements import (
    MeasuredValue,
    MeasurementSet,
    ResolvedPoint,
    _number_and_unit,
    _time_conflicts,
    default_sigma,
    load_measurements,
    measurable_paths,
    parse_settings,
    resolve_value,
)
from worldparts.system import System
from worldparts.units import parse_duration

__all__ = [
    "AT_BOUND",
    "DIFF_STEP",
    "MAX_AUTO_STEPS",
    "METHODS",
    "NULL_ABSOLUTE",
    "NULL_COMPONENT",
    "NULL_RELATIVE",
    "SCALE_FLOOR",
    "START_MARGIN",
    "STEP_TOLERANCE",
    "VERDICTS",
    "WEAK_CORRELATION",
    "WEAK_SE_FRACTION",
    "CalibrationResult",
    "IdentifiabilityReport",
    "ParameterEstimate",
    "PathFit",
    "Residual",
    "SensorCandidate",
    "calibrate",
    "identifiability",
]

#: Verdicts, from best to worst determined.
VERDICTS: tuple[str, ...] = ("identifiable", "weak", "not_identifiable")
#: ``weak`` when the standard error exceeds this fraction of the bound range.
WEAK_SE_FRACTION: float = 0.25
#: ``weak`` when the correlation with another parameter exceeds this in magnitude.
WEAK_CORRELATION: float = 0.95
#: Singular values below this fraction of the largest span the null space.
NULL_RELATIVE: float = 1e-8
#: A direction along which moving the parameters across their whole bound ranges changes the
#: normalised residuals by less than this (in norm) is null too: the data cannot tell those
#: values apart (its standard error would exceed 1000 bound ranges).
NULL_ABSOLUTE: float = 1e-3
#: A parameter whose component in the null space exceeds this is ``not_identifiable``.
NULL_COMPONENT: float = 0.01
#: Finite-difference step as a fraction of each parameter's scale (:func:`_scales`).
DIFF_STEP: float = 1e-3
#: A parameter's scale is at least this fraction of its bound range when the range includes
#: zero (where its magnitude can vanish).
SCALE_FLOOR: float = 0.01
#: A fitted value within this fraction of the bound range of a bound is "at the bound".
AT_BOUND: float = 1e-6
#: The optimiser starts this fraction of each bound range inside the bounds, but never
#: further than the parameter's own scale (:func:`_scales`) from its starting value.
START_MARGIN: float = 0.01
#: Timed points: halving the simulation step may change the predictions by at most this, in
#: the norm of the normalised residuals, so that the discretisation error moves no estimate
#: by more than about this many standard errors.
STEP_TOLERANCE: float = 0.1
#: The automatic step is not refined (or checked) into simulations of more steps than this.
MAX_AUTO_STEPS: int = 20_000
#: At most this many refits with a halved step when the fitted values need a finer one.
MAX_STEP_REFITS: int = 3
#: Optimisation methods of ``scipy.optimize.least_squares`` that accept bounds.
METHODS: tuple[str, ...] = ("trf", "dogbox")

_MAX_FAILURE_EXAMPLES = 5
#: At most this many equivalent candidates are listed per candidate sensor.
MAX_EQUIVALENT = 8
#: The most steps ``System.simulate`` runs in one call.
_SIMULATION_STEP_LIMIT = 1_000_000
#: Errors that mean "the model cannot be evaluated at these values". Request errors (bad
#: events, too many steps) are raised before the fit by :meth:`_Model._check_plan`.
_MODEL_FAILURES = (SolverError, SystemCheckError, InvalidValueError)


# ----------------------------------------------------------------------------------------
# results
# ----------------------------------------------------------------------------------------
def _num(x: Any) -> Any:
    """JSON-safe number: NaN and infinities become None."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


@dataclass(frozen=True)
class ParameterEstimate:
    """A calibrated parameter, or a parameter assessed by :func:`identifiability`.

    Every number of an entry is in ``unit``.

    Attributes:
        path: Parameter or input path.
        value: The fitted value (calibration) or the current value at which the sensors'
            sensitivity is evaluated (identifiability).
        unit: Declared unit of the parameter.
        lower: Lower bound of the fit.
        upper: Upper bound of the fit.
        standard_error: One standard deviation of the estimate (local, linearised); None
            when the parameter is not identifiable.
        verdict: ``identifiable``, ``weak`` or ``not_identifiable``.
        reason: One sentence saying why.
        initial: The starting value (calibration only).
        at_bound: ``"lower"`` or ``"upper"`` when the fitted value sits on a bound and the data
            push it further (without the bound the fit would move it outwards; a note says
            how far, in standard errors), and ``"model_limit"`` when it sits next to values
            at which the model cannot be evaluated (for example a tank level above the
            tank's height); calibration only. A parameter the data do not respond to is
            never flagged: it was not fitted.
        reference: Pressure reference (``gauge``, ``absolute``, ``difference``) or None.
    """

    path: str
    value: float
    unit: str
    lower: float
    upper: float
    standard_error: float | None
    verdict: str
    reason: str
    initial: float | None = None
    at_bound: str | None = None
    reference: str | None = None

    @property
    def relative_error(self) -> float | None:
        """Standard error as a fraction of the bound range (None when not identifiable)."""
        if self.standard_error is None:
            return None
        return self.standard_error / (self.upper - self.lower)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; every number is in ``unit``."""
        out: dict[str, Any] = {
            "value": _num(self.value),
            "unit": self.unit,
            "standard_error": _num(self.standard_error),
            "lower": _num(self.lower),
            "upper": _num(self.upper),
            "verdict": self.verdict,
            "reason": self.reason,
        }
        if self.initial is not None:
            out["initial"] = _num(self.initial)
        if self.at_bound is not None:
            out["at_bound"] = self.at_bound
        if self.reference:
            out["reference"] = self.reference
        return out


@dataclass(frozen=True)
class Residual:
    """One measured value against the model at the fitted parameters.

    Attributes:
        point: Point name.
        path: Measured variable.
        measured: Measured value, in ``unit`` (the variable's display unit).
        predicted: Model value at the fitted parameters, in ``unit`` (None if undefined).
        sigma: Standard uncertainty used, in ``unit``.
        unit: Display unit.
        time: Time in s for a point of a time series, else None.
        reference: Pressure reference of the values, if any.
        sigma_default: True when ``sigma`` is the default uncertainty.
    """

    point: str
    path: str
    measured: float
    predicted: float | None
    sigma: float
    unit: str
    time: float | None = None
    reference: str | None = None
    sigma_default: bool = False

    @property
    def residual(self) -> float | None:
        """``measured - predicted`` in ``unit``."""
        return None if self.predicted is None else self.measured - self.predicted

    @property
    def normalised(self) -> float | None:
        """The residual divided by ``sigma`` (dimensionless)."""
        r = self.residual
        return None if r is None else r / self.sigma

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; ``measured``, ``predicted``, ``residual`` and ``sigma`` are in
        ``unit``, ``normalised`` is dimensionless and ``time`` is in s."""
        out: dict[str, Any] = {"point": self.point, "path": self.path}
        if self.time is not None:
            out["time"] = self.time
        out.update(
            {
                "measured": _num(self.measured),
                "predicted": _num(self.predicted),
                "residual": _num(self.residual),
                "sigma": _num(self.sigma),
                "unit": self.unit,
                "normalised": _num(self.normalised),
            }
        )
        if self.reference:
            out["reference"] = self.reference
        if self.sigma_default:
            out["sigma_default"] = True
        return out


@dataclass(frozen=True)
class PathFit:
    """How well the fitted model matches one measured path over all points.

    Attributes:
        path: Measured variable.
        unit: Its display unit.
        count: Number of measured values.
        rms: Root mean square of the residuals, in ``unit``.
        normalised_rms: Root mean square of the normalised residuals (about 1 when the
            model fits within the stated uncertainties).
        max_abs_normalised: Largest normalised residual in magnitude.
    """

    path: str
    unit: str
    count: int
    rms: float
    normalised_rms: float
    max_abs_normalised: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; ``rms`` is in ``unit``, the normalised values are
        dimensionless."""
        return {
            "count": self.count,
            "rms": _num(self.rms),
            "unit": self.unit,
            "normalised_rms": _num(self.normalised_rms),
            "max_abs_normalised": _num(self.max_abs_normalised),
        }


@dataclass(frozen=True)
class SensorCandidate:
    """The effect of adding one candidate sensor (:func:`identifiability`).

    Attributes:
        sensor: Candidate path.
        sensor_unit: The candidate's display unit.
        parameter: The worst-determined parameter without the candidate.
        parameter_unit: That parameter's declared unit, the unit of ``standard_error``.
        standard_error: That parameter's standard error with the candidate added, in
            ``parameter_unit`` (None when it stays not identifiable).
        relative_error: The same as a fraction of its bound range (None when not
            identifiable).
        verdict: That parameter's verdict with the candidate added.
        identifiable: How many parameters are ``identifiable`` with the candidate added.
        equivalent: Other candidates with the same effect (within 1 %), for example the
            same flow read on another component in series.
        sensor_reference: The candidate's pressure reference, if any.
        parameter_reference: The parameter's pressure reference, if any.
    """

    sensor: str
    sensor_unit: str
    parameter: str
    parameter_unit: str
    standard_error: float | None
    relative_error: float | None
    verdict: str
    identifiable: int
    equivalent: tuple[str, ...] = ()
    sensor_reference: str | None = None
    parameter_reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; ``standard_error`` is in ``parameter_unit``."""
        out: dict[str, Any] = {"sensor": self.sensor, "sensor_unit": self.sensor_unit}
        if self.sensor_reference:
            out["sensor_reference"] = self.sensor_reference
        out.update(
            {
                "parameter": self.parameter,
                "parameter_unit": self.parameter_unit,
                "standard_error": _num(self.standard_error),
                "relative_error": _num(self.relative_error),
                "verdict": self.verdict,
                "identifiable": self.identifiable,
            }
        )
        if self.parameter_reference:
            out["parameter_reference"] = self.parameter_reference
        if self.equivalent:
            out["equivalent"] = list(self.equivalent)
        return out


def _corr_dict(names: Sequence[str], corr: np.ndarray) -> dict[str, dict[str, float | None]]:
    return {a: {b: _num(corr[i, j]) for j, b in enumerate(names)} for i, a in enumerate(names)}


@dataclass
class CalibrationResult:
    """The result of :func:`calibrate`.

    Attributes:
        parameters: Path to :class:`ParameterEstimate` (fitted value, standard error,
            verdict, bounds, starting value, at-bound flag).
        residuals: Every measured value with the model value at the fit, point by point.
        paths: Measured path to :class:`PathFit` (RMS of residuals and normalised residuals).
        chi_square: Sum of squared normalised residuals at the fit.
        initial_chi_square: The same at the starting values.
        dof: Degrees of freedom: measured values minus the number of parameter
            combinations the data determine (the rank of the Jacobian; the number of
            parameters unless some are not identifiable).
        reduced_chi_square: ``chi_square / dof`` (None when ``dof <= 0``); about 1 when the
            model explains the data within the stated uncertainties.
        p_value: Probability of a chi-square at least this large if the model and the
            uncertainties are right (None when ``dof <= 0``); below about 0.01 the misfit is
            real.
        error_scale: ``s``, the factor applied to the standard errors: the square root of
            the reduced chi-square when it exceeds 1, else 1.
        correlation: Path to path to the correlation of the estimates (None for a pair
            that involves a non-identifiable parameter).
        singular_values: Singular values of the Jacobian of the normalised residuals with
            respect to the parameters scaled by their bound ranges, largest first.
        condition_number: Largest over smallest singular value (None when infinite).
        null_directions: Each null direction of that Jacobian as ``{"singular_value",
            "combination": {path: weight}}``, weights in units of each bound range: moving
            the parameters together in these proportions leaves every predicted value
            unchanged to first order.
        success: Whether the optimiser reported convergence at a point at least as good
            as the start (False when it ended worse and the starting values are reported;
            a note says so).
        message: The optimiser's termination message.
        method: ``trf`` or ``dogbox``.
        evaluations: Model evaluations (each solves every steady point and simulates the
            timed points: twice, at ``step`` and ``step / 2``, when extrapolated).
        failures: Model evaluations that failed during the fit.
        failure_examples: The first few failure messages.
        applied: Whether the fitted values were left in the system (``apply=True``).
        step: Simulation step of the timed points in s (None without timed points).
        step_extrapolated: True when the timed predictions are extrapolated to zero step
            from simulations at ``step`` and ``step / 2`` (the default); False for a step
            given explicitly.
        step_change: How much the timed predictions at the fitted values change when the
            step is halved (norm of the normalised residuals): an estimate of their
            discretisation error, which can move an estimate by about that many standard
            errors. At most :data:`STEP_TOLERANCE` unless a note says otherwise; None when
            it was not checked or there are no timed points.
        notes: Plain-language remarks for an agent (defaults used, misfit, bounds hit).
    """

    parameters: dict[str, ParameterEstimate]
    residuals: list[Residual]
    paths: dict[str, PathFit]
    chi_square: float
    initial_chi_square: float
    dof: int
    reduced_chi_square: float | None
    p_value: float | None
    error_scale: float
    correlation: dict[str, dict[str, float | None]]
    singular_values: list[float]
    condition_number: float | None
    null_directions: list[dict[str, Any]]
    success: bool
    message: str
    method: str
    evaluations: int
    failures: int = 0
    failure_examples: list[str] = field(default_factory=list)
    applied: bool = False
    step: float | None = None
    step_extrapolated: bool = False
    step_change: float | None = None
    notes: list[str] = field(default_factory=list)

    def __getitem__(self, path: str) -> float:
        """Fitted value of ``path`` in its declared unit."""
        try:
            return self.parameters[path].value
        except KeyError:
            raise UnknownVariableError(
                f"'{path}' was not calibrated. " + format_choices(path, self.parameters)
            ) from None

    @property
    def values(self) -> dict[str, float]:
        """Path to fitted value (declared units), e.g. for ``System.set_values``."""
        return {p: e.value for p, e in self.parameters.items()}

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict: every value carries its unit; NaN and infinities are None."""
        return {
            "success": self.success,
            "message": self.message,
            "method": self.method,
            "applied": self.applied,
            "parameters": {p: e.to_dict() for p, e in self.parameters.items()},
            "chi_square": _num(self.chi_square),
            "initial_chi_square": _num(self.initial_chi_square),
            "dof": self.dof,
            "reduced_chi_square": _num(self.reduced_chi_square),
            "p_value": _num(self.p_value),
            "error_scale": _num(self.error_scale),
            "paths": {p: f.to_dict() for p, f in self.paths.items()},
            "residuals": [r.to_dict() for r in self.residuals],
            "correlation": self.correlation,
            "singular_values": [_num(s) for s in self.singular_values],
            "condition_number": _num(self.condition_number),
            "null_directions": self.null_directions,
            "evaluations": self.evaluations,
            "failures": {"count": self.failures, "examples": list(self.failure_examples)},
            "step": None
            if self.step is None
            else {
                "value": _num(self.step),
                "unit": "s",
                "extrapolated": self.step_extrapolated,
                "change_when_halved": _num(self.step_change),
            },
            "notes": list(self.notes),
        }


@dataclass
class IdentifiabilityReport:
    """The result of :func:`identifiability`.

    Attributes:
        parameters: Path to :class:`ParameterEstimate` at the current values: the standard
            error the sensors would give with default uncertainties, and the verdict.
        sensors: The sensor paths analysed.
        points: Names of the operating points.
        correlation: As in :class:`CalibrationResult`.
        singular_values: As in :class:`CalibrationResult`.
        condition_number: As in :class:`CalibrationResult`.
        null_directions: As in :class:`CalibrationResult`.
        worst: The worst-determined parameter (largest standard error relative to its
            bound range; a non-identifiable one first), or None.
        recommendation: The candidate sensor whose addition most improves ``worst``, or
            None when no candidate improves it.
        candidates: Every usable candidate, best first.
        step: Simulation step of the timed points in s, chosen as in :func:`calibrate`
            (None without timed points).
        notes: Plain-language remarks for an agent.
    """

    parameters: dict[str, ParameterEstimate]
    sensors: list[str]
    points: list[str]
    correlation: dict[str, dict[str, float | None]]
    singular_values: list[float]
    condition_number: float | None
    null_directions: list[dict[str, Any]]
    worst: str | None
    recommendation: SensorCandidate | None
    candidates: list[SensorCandidate]
    step: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self, max_candidates: int | None = 10) -> dict[str, Any]:
        """JSON-ready dict with at most ``max_candidates`` ranked candidates."""
        shown = self.candidates if max_candidates is None else self.candidates[:max_candidates]
        return {
            "parameters": {p: e.to_dict() for p, e in self.parameters.items()},
            "sensors": list(self.sensors),
            "points": list(self.points),
            "correlation": self.correlation,
            "singular_values": [_num(s) for s in self.singular_values],
            "condition_number": _num(self.condition_number),
            "null_directions": self.null_directions,
            "worst": self.worst,
            "recommendation": None
            if self.recommendation is None
            else self.recommendation.to_dict(),
            "candidates": [c.to_dict() for c in shown],
            "candidates_total": len(self.candidates),
            "step": None if self.step is None else {"value": _num(self.step), "unit": "s"},
            "notes": list(self.notes),
        }


# ----------------------------------------------------------------------------------------
# parameters
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Param:
    path: str
    unit: str
    reference: str | None
    lower: float
    upper: float
    current: float

    @property
    def range(self) -> float:
        return self.upper - self.lower


_PARAM_KEYS = ("path", "lower", "upper")


def _parameter_entries(parameters: Any) -> list[tuple[str, Any, Any, str]]:
    """``(path, lower, upper, where)`` from the accepted forms of ``parameters``."""
    if isinstance(parameters, str):
        return [(parameters, None, None, "parameters[0]")]
    if isinstance(parameters, Mapping) and "path" not in parameters:
        items: list[Any] = []
        for path, bounds in parameters.items():
            if bounds is None:
                items.append({"path": path})
            elif isinstance(bounds, Mapping):
                items.append({**bounds, "path": path})
            elif isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                items.append({"path": path, "lower": bounds[0], "upper": bounds[1]})
            else:
                raise InvalidValueError(
                    f"parameters['{path}']: give bounds as [lower, upper] or {{lower, upper}} "
                    f"(or None for the hard limits), got {bounds!r}."
                )
        parameters = items
    elif isinstance(parameters, Mapping):
        parameters = [parameters]
    if not isinstance(parameters, Iterable):
        raise InvalidValueError(
            "parameters must be a list such as [{'path': 'pump.wear_head', 'lower': 0, "
            f"'upper': 0.5}}] or ['pump.wear_head'], got {type(parameters).__name__}."
        )
    out: list[tuple[str, Any, Any, str]] = []
    for i, entry in enumerate(parameters):
        where = f"parameters[{i}]"
        if isinstance(entry, str):
            out.append((entry, None, None, where))
            continue
        if not isinstance(entry, Mapping) or "path" not in entry:
            raise InvalidValueError(
                f"{where}: expected a path or {{path, lower?, upper?}}, e.g. "
                f"{{'path': 'pump.wear_head', 'lower': 0, 'upper': 0.5}}; got {entry!r}."
            )
        unknown = sorted(str(k) for k in entry if k not in _PARAM_KEYS)
        if unknown:
            raise InvalidValueError(
                f"{where}: unknown key(s) {', '.join(map(repr, unknown))}; a parameter is "
                "{path, lower?, upper?}."
            )
        out.append((str(entry["path"]), entry.get("lower"), entry.get("upper"), where))
    return out


def _resolve_parameters(
    system: System, parameters: Any, points: Sequence[ResolvedPoint]
) -> list[_Param]:
    entries = _parameter_entries(parameters)
    if not entries:
        raise InvalidValueError(
            "No parameters to calibrate; give at least one, e.g. "
            "[{'path': 'pump.wear_head', 'lower': 0, 'upper': 0.5}]."
        )
    controlled = {c.actuate: name for name, c in system.controls.items()}
    out: list[_Param] = []
    seen: set[str] = set()
    for path, lo_raw, hi_raw, where in entries:
        if path in seen:
            raise InvalidValueError(f"{where}: {path} is listed twice.")
        seen.add(path)
        inst, name = system._var_path(path)
        m = inst.manifest
        spec = m.parameters.get(name) or m.inputs.get(name)
        numeric = [
            f"{inst.name}.{n}"
            for group in (m.parameters, m.inputs)
            for n, s in group.items()
            if s.type == "number"
        ]
        if spec is None:
            if name in m.states:
                raise InvalidValueError(
                    f"{where}: {path} is a state; calibrate the parameter or input that sets "
                    "it (for a tank level, its initial_level) or measure it. Numeric "
                    f"parameters and inputs of {inst.name}: {', '.join(numeric)}."
                )
            what = (
                "is read-only (an observable or port variable)"
                if (name in m.observables or "." in name)
                else "does not exist"
            )
            raise UnknownVariableError(
                f"{where}: {path} {what}; only parameters and inputs can be calibrated. "
                + format_choices(path, numeric)
            )
        if spec.type != "number":
            raise InvalidValueError(
                f"{where}: {path} is a {spec.type} parameter; only number parameters and "
                f"inputs can be calibrated. Numeric parameters and inputs of {inst.name}: "
                + ", ".join(numeric)
                + "."
            )
        if path in controlled:
            raise InvalidValueError(
                f"{where}: {path} is written by control '{controlled[path]}', which would "
                "override every trial value. Calibrate another path or remove the control."
            )
        for p in points:
            if path in p.settings:
                raise InvalidValueError(
                    f"{where}: point '{p.name}' sets {path}, which is being calibrated; remove "
                    "it from the point's settings or from the parameters."
                )
        lower = spec.minimum if lo_raw is None else spec.parse(lo_raw, f"{where}.lower")
        upper = spec.maximum if hi_raw is None else spec.parse(hi_raw, f"{where}.upper")
        for bound, side in ((lower, "lower"), (upper, "upper")):
            if bound is None:
                raise InvalidValueError(
                    f"{where}: {path} has no hard {side} limit; give '{side}' explicitly, e.g. "
                    f"{{'path': '{path}', 'lower': ..., 'upper': ...}}."
                )
        assert lower is not None and upper is not None
        if not float(lower) < float(upper):
            raise InvalidValueError(
                f"{where}: the bounds of {path} must satisfy lower < upper; got lower = "
                f"{lower:g} and upper = {upper:g} ({spec.unit}). Its hard limits are "
                f"{spec.limits_text()}."
            )
        unit = spec.unit or "1"
        out.append(
            _Param(path, unit, spec.reference, float(lower), float(upper), float(system.get(path)))
        )
    return out


# ----------------------------------------------------------------------------------------
# model evaluation
# ----------------------------------------------------------------------------------------
class _ModelFailure(Exception):
    """The model could not be evaluated at some parameter values."""


def _scales(x: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> np.ndarray:
    """Each parameter's scale at ``x``, in its unit: its magnitude, at most its bound range,
    and at least :data:`SCALE_FLOOR` of the range when the range includes zero (where the
    magnitude can vanish).

    Finite-difference steps, the optimiser's variable scales and the start margin follow
    it, so a wide bound range (for example a Kv's hard limits [1e-4, 1e5]) does not make
    them coarse: they stay relative to the value, as the model's own behaviour does.
    """
    rng = upper - lower
    floor = np.where((lower <= 0.0) & (upper >= 0.0), SCALE_FLOOR * rng, 0.0)
    return np.minimum(rng, np.maximum(np.abs(x), floor))


def _initial_step(system: System, times: Sequence[float]) -> float:
    """First step of the automatic choice: the median interval between successive
    measurement times (counted from 0), at most the document's ``simulation.step``.

    The median, so that one close pair of readings does not set the step of the whole run;
    the accuracy check refines it from there.
    """
    marks = sorted({0.0, *times})
    gaps = [b - a for a, b in pairwise(marks) if b - a > 0]
    step = float(np.median(gaps)) if gaps else 1.0
    if system.simulation and system.simulation.get("step") is not None:
        document = parse_duration(system.simulation["step"], "simulation.step")
        if 0 < document < step:
            step = document
    return step


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.6g} s"


class _Model:
    """Predicts measured paths at operating points for trial parameter values.

    Every evaluation starts from the system's values at construction (parameters, inputs,
    states and control switch states), so evaluations are independent; :meth:`reset` puts
    them back for good.

    Timed points run one simulation. With an automatic step (``step`` None) their
    predictions are extrapolated to zero step from simulations at ``self.step`` and half of
    it (``2 P(h/2) - P(h)``, removing the first-order error of explicit Euler); with a
    given step they are the plain simulation at that step, as ``System.simulate`` gives.
    """

    def __init__(
        self,
        system: System,
        params: Sequence[_Param],
        points: Sequence[ResolvedPoint],
        wanted: Mapping[int, Sequence[str]],
        step: float | None,
    ) -> None:
        self.system = system
        self.params = list(params)
        self.points = list(points)
        self.wanted = {i: list(dict.fromkeys(paths)) for i, paths in wanted.items() if paths}
        self.saved = system._save_values()
        self.saved_controls = {n: c.state for n, c in system.controls.items()}
        self.steady = [i for i, p in enumerate(points) if p.time is None and i in self.wanted]
        timed = [i for i, p in enumerate(points) if p.time is not None]
        self.timed = [i for i in timed if i in self.wanted]
        self.evaluations = 0
        self.failures = 0
        self.failure_examples: list[str] = []
        self.last_failure = ""
        self.duration = 0.0
        self.step = 1.0
        self.given_step = step is not None
        self.extrapolate = False
        self.events: list[dict[str, Any]] = []
        self.series: list[str] = []
        self._runs: dict[tuple[Any, ...], dict[tuple[int, str], float | None]] = {}
        if self.timed:
            times = [float(points[i].time) for i in timed if points[i].time is not None]
            self.duration = max(times)
            if step is not None:
                self.step = step
            else:
                self.step = _initial_step(system, times)
                if self.duration > 0:
                    self.step = min(self.step, self.duration)
                    self.extrapolate = True
            by_time: dict[float, dict[str, Any]] = {}
            for i in timed:
                if points[i].settings:
                    by_time.setdefault(float(points[i].time or 0.0), {}).update(points[i].settings)
            self.events = [{"at": t, "set": s} for t, s in sorted(by_time.items())]
            self.series = sorted({p for i in self.timed for p in self.wanted[i]})
            self._check_plan()

    def _steps_at(self, step: float) -> int:
        """Number of steps of one simulation of the timed points at ``step``."""
        return math.floor(self.duration / step + 1e-9) + len(self.events)

    def _check_plan(self) -> None:
        """Validate the simulation of the timed points once, so that a bad request (too many
        steps, settings that cannot be events) is reported as such, not as a model failure
        at the starting values.

        Raises:
            InvalidValueError: The simulation at half the step (the finer run of an
                extrapolated evaluation, or the check of a given step) would exceed
                ``System.simulate``'s step limit.
            MeasurementError: The settings of the timed points cannot be applied as events.
        """
        n = self._steps_at(self.step / 2)
        if n > _SIMULATION_STEP_LIMIT:
            what = (
                f"step={_fmt_s(self.step)}"
                if self.given_step
                else f"the automatic step {_fmt_s(self.step)} (the document's simulation.step "
                "or the median interval between measurement times)"
            )
            raise InvalidValueError(
                f"The timed points span {_fmt_s(self.duration)}; with {what}, a simulation at "
                f"half the step takes {n} steps, above the limit of {_SIMULATION_STEP_LIMIT:,}. "
                "Give a larger step=."
            )
        try:
            self.system._parse_events(self.events, self.duration, self.step, 1e-9 * self.step)
        except WorldpartsError as exc:
            raise MeasurementError(
                "The settings of the timed points cannot be applied as events:", [str(exc)]
            ) from None

    def can_check(self) -> bool:
        """Whether the step can be checked by halving it within :data:`MAX_AUTO_STEPS` (a
        given step is always checked: its half was validated up front)."""
        if not self.timed:
            return False
        if not self.extrapolate:
            return True
        return self._steps_at(self.step / 4) <= MAX_AUTO_STEPS

    def can_refine(self) -> bool:
        """Whether the automatic step can be halved and still be checked within
        :data:`MAX_AUTO_STEPS`."""
        return self.extrapolate and self._steps_at(self.step / 8) <= MAX_AUTO_STEPS

    def reset(self) -> None:
        """Put the system back to its values at construction."""
        self.system._restore_values(self.saved)
        controls = self.system.controls
        for name, state in self.saved_controls.items():
            if name in controls:
                controls[name].state = state

    def values_at(self, x: np.ndarray) -> dict[str, float]:
        """Trial values as a ``set_values`` mapping (clipped into the bounds)."""
        return {
            p.path: float(min(max(v, p.lower), p.upper))
            for p, v in zip(self.params, x, strict=True)
        }

    def predict(
        self, x: np.ndarray, step: float | None = None
    ) -> dict[tuple[int, str], float | None]:
        """Model values of every wanted (point, path) at parameter values ``x``.

        Args:
            x: Parameter values in their declared units.
            step: Simulation step of the timed points (default ``self.step``).

        Raises:
            _ModelFailure: When a solve or the simulation fails at these values.
        """
        self.evaluations += 1
        theta = self.values_at(x)
        out: dict[tuple[int, str], float | None] = {}
        try:
            for i in self.steady:
                point = self.points[i]
                self.reset()
                try:
                    self.system.set_values({**theta, **point.settings})
                    result = self.system._solve([])  # checked once by the caller
                except _MODEL_FAILURES as exc:
                    raise _ModelFailure(f"point '{point.name}': {exc}") from exc
                for path in self.wanted[i]:
                    value = result.values.get(path)
                    out[(i, path)] = None if value is None else float(value)
            if self.timed:
                h = self.step if step is None else step
                if self.extrapolate:
                    coarse, fine = self._run(theta, h), self._run(theta, h / 2)
                    for key, a in coarse.items():
                        b = fine[key]
                        out[key] = None if a is None or b is None else 2.0 * b - a
                else:
                    out.update(self._run(theta, h))
        except _ModelFailure as exc:
            self.failures += 1
            shown = ", ".join(f"{p} = {v:.6g}" for p, v in theta.items())
            self.last_failure = f"at {shown}: {exc}"
            if len(self.failure_examples) < _MAX_FAILURE_EXAMPLES:
                self.failure_examples.append(self.last_failure)
            raise
        return out

    def _run(self, theta: Mapping[str, float], step: float) -> dict[tuple[int, str], float | None]:
        """The timed points' values from one simulation at ``step`` (the last few runs are
        cached, since an extrapolated evaluation and a step check share one)."""
        key = (tuple(theta.values()), step)
        cached = self._runs.get(key)
        if cached is not None:
            return cached
        self.reset()
        try:
            self.system.set_values(dict(theta))
            sim = self.system.simulate(
                duration=self.duration, step=step, events=self.events, variables=self.series
            )
        except _MODEL_FAILURES as exc:
            raise _ModelFailure(f"simulation of the timed points: {exc}") from exc
        tol = 1e-9 * max(1.0, step)
        values = {
            (i, path): _sample(sim.time, sim.series[path], float(self.points[i].time or 0.0), tol)
            for i in self.timed
            for path in self.wanted[i]
        }
        if len(self._runs) >= 8:
            del self._runs[next(iter(self._runs))]
        self._runs[key] = values
        return values


def _sample(
    times: Sequence[float], series: Sequence[float | None], t: float, tol: float
) -> float | None:
    """The series value at ``t``: the sample at ``t``, or linear interpolation between the
    samples around it."""
    k = bisect_left(times, t - tol)
    if k < len(times) and abs(times[k] - t) <= tol:
        v = series[k]
        return None if v is None else float(v)
    if k == 0 or k >= len(times):
        return None
    a, b = series[k - 1], series[k]
    if a is None or b is None:
        return None
    w = (t - times[k - 1]) / (times[k] - times[k - 1])
    return float(a) + w * (float(b) - float(a))


class _Objective:
    """Predictions of a list of (point, path) keys as a vector, in bound-range coordinates
    ``z`` (``x = lower + z * range``), with finite-difference Jacobians."""

    def __init__(
        self,
        model: _Model,
        keys: Sequence[tuple[int, str]],
        required: Sequence[bool] | None = None,
    ) -> None:
        self.model = model
        self.keys = list(keys)
        self.lower = np.array([p.lower for p in model.params])
        self.upper = np.array([p.upper for p in model.params])
        self.range = self.upper - self.lower
        #: Rows that must be defined for an evaluation to count (the others may be NaN).
        self.required = np.ones(len(self.keys), bool) if required is None else np.array(required)
        self._cache: dict[tuple[float, bytes], np.ndarray | None] = {}

    def x(self, z: np.ndarray) -> np.ndarray:
        return self.lower + np.clip(z, 0.0, 1.0) * self.range

    def scales(self, z: np.ndarray) -> np.ndarray:
        """Each parameter's scale (:func:`_scales`) at ``z``, in bound-range units."""
        return _scales(self.x(z), self.lower, self.upper) / self.range

    def predict(self, z: np.ndarray, step: float | None = None) -> np.ndarray:
        """Predicted values (NaN where a value is undefined), with the model's step or
        ``step`` for the timed points.

        Raises:
            _ModelFailure: When the model fails at ``z``.
        """
        h = self.model.step if step is None else step
        key = (h, np.asarray(z, dtype=float).tobytes())
        if key in self._cache:
            cached = self._cache[key]
            if cached is None:
                raise _ModelFailure("the model failed at these values (cached)")
            return cached
        if len(self._cache) > 64:
            self._cache.clear()
        try:
            values = self.model.predict(self.x(z), h)
        except _ModelFailure:
            self._cache[key] = None
            raise
        out = np.array(
            [np.nan if values.get(k) is None else values[k] for k in self.keys], dtype=float
        )
        self._cache[key] = out
        return out

    def _try(self, z: np.ndarray) -> np.ndarray | None:
        try:
            p = self.predict(z)
        except _ModelFailure:
            return None
        return p if np.all(np.isfinite(p[self.required])) else None

    def jacobian_forward(self, z: np.ndarray, p0: np.ndarray) -> np.ndarray:
        """Forward differences (backward at the upper bound or where forward fails), with a
        step of :data:`DIFF_STEP` of each parameter's scale.

        A column whose model evaluations fail in both directions is zero (the optimiser
        then keeps that parameter still for the step).
        """
        jac = np.zeros((len(self.keys), len(z)))
        steps = DIFF_STEP * self.scales(z)
        for j in range(len(z)):
            h = float(steps[j])
            first = 1.0 if z[j] + h <= 1.0 else -1.0
            for d in (first, -first):
                zz = np.array(z, dtype=float)
                zz[j] = z[j] + d * h
                if not 0.0 <= zz[j] <= 1.0:
                    continue
                p = self._try(zz)
                if p is not None:
                    jac[:, j] = (p - p0) / (d * h)
                    break
        return jac

    def jacobian_accurate(
        self, z: np.ndarray, p0: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, list[int]]:
        """Richardson-extrapolated finite differences and a bound on their error.

        Each parameter's step ``h`` is :data:`DIFF_STEP` of its scale. Where the bounds
        allow ``z +- 4h``, central differences ``D(h)``, ``D(2h)`` and ``D(4h)`` give the
        Jacobian ``(4 D(h) - D(2h)) / 3`` (error of order ``h**4``) and a second estimate
        ``(4 D(2h) - D(4h)) / 3`` whose truncation error is 16 times larger. Elsewhere
        one-sided differences at ``h``, ``2h``, ``4h`` and ``8h`` are extrapolated twice
        (order ``h**3``), with the second estimate one level up (8 times the error). The
        difference of the two estimates is the error bound: at most 15 (or 7) times the
        truncation error of the Jacobian used, and about its size when solver round-off
        dominates.

        Returns:
            The Jacobian of the predictions with respect to ``z``, its error bound, and
            per parameter the side (+1 or -1) on which the model failed, forcing one-sided
            differences (0 when it did not): the point sits at the edge of the region
            where the model can be evaluated.

        Raises:
            CalibrationError: When the model fails on both sides of a parameter.
        """
        m, n = len(self.keys), len(z)
        jac = np.zeros((m, n))
        err = np.zeros((m, n))
        edges = [0] * n
        steps = DIFF_STEP * self.scales(z)
        for j in range(n):
            h = float(steps[j])

            def at(offset: float, j: int = j) -> np.ndarray | None:
                zz = np.array(z, dtype=float)
                zz[j] = z[j] + offset
                return self._try(zz)

            failed: set[float] = set()
            done = False
            if z[j] - 4 * h >= 0.0 and z[j] + 4 * h <= 1.0:
                f = {k: at(k * h) for k in (1, 2, 4, -1, -2, -4)}
                if any(f[k] is None for k in (1, 2, 4)):
                    failed.add(1.0)
                if any(f[k] is None for k in (-1, -2, -4)):
                    failed.add(-1.0)
                if not failed:
                    d = {k: (f[k] - f[-k]) / (2 * k * h) for k in (1, 2, 4)}
                    jac[:, j] = (4 * d[1] - d[2]) / 3
                    err[:, j] = np.abs(jac[:, j] - (4 * d[2] - d[4]) / 3)
                    done = True
            if not done:
                sides = [1.0, -1.0] if z[j] + 8 * h <= 1.0 else [-1.0, 1.0]
                sides.sort(key=lambda s: s in failed)
                for s in sides:
                    if not 0.0 <= z[j] + 8 * s * h <= 1.0:
                        continue
                    g = {k: at(k * s * h) for k in (1, 2, 4, 8)}
                    if any(v is None for v in g.values()):
                        failed.add(s)
                        continue
                    dk = {k: (g[k] - p0) / (k * s * h) for k in g}
                    a = {k: 2 * dk[k] - dk[2 * k] for k in (1, 2, 4)}  # order h**2
                    jac[:, j] = (4 * a[1] - a[2]) / 3  # order h**3
                    err[:, j] = np.abs(jac[:, j] - (4 * a[2] - a[4]) / 3)
                    edges[j] = int(-s) if -s in failed else 0
                    done = True
                    break
            if not done:
                path = self.model.params[j].path
                raise CalibrationError(
                    f"The model fails on both sides of {path} next to the point being "
                    "analysed, so its sensitivity cannot be computed. Last failure: "
                    f"{self.model.last_failure}"
                )
        return jac, err, edges


def _step_change(
    obj: _Objective, z: np.ndarray, sigma: np.ndarray, rows: np.ndarray | None = None
) -> float:
    """Norm of the change of the normalised predictions at ``z`` when the step of the timed
    points is halved (only ``rows`` when given); infinite when a value is undefined.

    Raises:
        _ModelFailure: When the model fails at ``z`` at either step.
    """
    h = obj.model.step
    d = (obj.predict(z) - obj.predict(z, h / 2)) / sigma
    if rows is not None:
        d = d[rows]
    return float(np.linalg.norm(d)) if np.all(np.isfinite(d)) else math.inf


def _choose_step(
    obj: _Objective, z: np.ndarray, sigma: np.ndarray, rows: np.ndarray | None = None
) -> float | None:
    """Halve the model's automatic step until halving it once more changes the predictions
    at ``z`` by at most :data:`STEP_TOLERANCE`, or until a further check would exceed
    :data:`MAX_AUTO_STEPS`. A step at which the model fails counts as too coarse.

    Returns:
        The last change measured (None when the step could not be checked at all).
    """
    model = obj.model
    change: float | None = None
    while model.can_check():
        try:
            change = _step_change(obj, z, sigma, rows)
        except _ModelFailure:
            change = math.inf
        if change <= STEP_TOLERANCE or not model.can_refine():
            break
        model.step /= 2
    return change


def _step_notes(model: _Model, change: float | None) -> list[str]:
    """Notes on the simulation step of the timed points when its accuracy is in doubt."""
    if not model.timed or model.duration == 0:
        return []
    how = (
        f"extrapolated to zero step from steps of {_fmt_s(model.step)} and {_fmt_s(model.step / 2)}"
        if model.extrapolate
        else f"with step={_fmt_s(model.step)} as given"
    )
    if change is None:
        return [
            f"The timed points were simulated {how}. The step was not checked for accuracy: "
            f"the check would take more than {MAX_AUTO_STEPS:,} simulation steps. Pass step= to "
            "choose one, and compare a fit at half of it."
        ]
    if change <= STEP_TOLERANCE:
        return []
    advice = (
        f"A finer automatic step would exceed {MAX_AUTO_STEPS:,} simulation steps or "
        f"{MAX_STEP_REFITS} refits; pass step= to choose one."
        if model.extrapolate
        else "Omit step to let calibrate choose an accurate one, or give a smaller one."
    )
    if math.isinf(change):
        return [
            f"The timed points were simulated {how}; the model could not be evaluated at "
            f"half the step, so the discretisation error of the predictions is unknown. {advice}"
        ]
    return [
        f"The timed points were simulated {how}; halving the step moves the predictions by "
        f"{change:.2g} (norm of the normalised residuals), so the estimates may be biased by up "
        f"to about that many standard errors. {advice}"
    ]


# ----------------------------------------------------------------------------------------
# Jacobian analysis
# ----------------------------------------------------------------------------------------
@dataclass
class _Analysis:
    se: np.ndarray
    corr: np.ndarray
    singular: np.ndarray
    null: list[tuple[float, np.ndarray]]
    not_identifiable: np.ndarray
    verdicts: list[str]
    reasons: list[str]
    #: Right singular vectors (columns), in bound-range units.
    v: np.ndarray
    null_mask: np.ndarray

    def relative(self, ranges: np.ndarray) -> np.ndarray:
        """Standard errors over bound ranges; infinite where not identifiable."""
        return np.where(self.not_identifiable, np.inf, self.se / ranges)

    def gauss_newton_step(self, jz: np.ndarray, f: np.ndarray) -> np.ndarray:
        """The unconstrained Gauss-Newton step from the analysed point, in bound-range
        units, within the directions the data determine: where the data would move the
        parameters if there were no bounds."""
        keep = ~self.null_mask
        if not keep.any():
            return np.zeros(self.v.shape[0])
        vk, sk = self.v[:, keep], self.singular[keep]
        return -vk @ (((jz @ vk).T @ f) / sk**2)


def _analyse(
    jz: np.ndarray,
    ez: np.ndarray,
    params: Sequence[_Param],
    s2: float,
) -> _Analysis:
    """Standard errors, correlations, singular values and verdicts from the Jacobian ``jz``
    of the normalised residuals with respect to the bound-range coordinates, and ``ez``,
    the bound on its numerical error (element by element).

    A singular direction ``v`` is null when its singular value is at or below the largest
    of :data:`NULL_RELATIVE` of the largest singular value, the numerical error of the
    Jacobian along it, ``|| ez |v| ||`` (so a badly differenced parameter does not drag the
    directions of the others into the null space), and :data:`NULL_ABSOLUTE`.
    """
    m, n = jz.shape
    ranges = np.array([p.range for p in params])
    if m < n:  # pad with zero rows so that V spans every parameter direction
        jz = np.vstack([jz, np.zeros((n - m, n))])
        ez = np.vstack([ez, np.zeros((n - m, n))])
    _, singular, vt = np.linalg.svd(jz, full_matrices=False)  # thin: never m x m
    v = vt.T
    smax = float(singular.max()) if n else 0.0
    along = np.linalg.norm(ez @ np.abs(v), axis=0)
    tol = np.maximum(np.maximum(NULL_RELATIVE * smax, along), NULL_ABSOLUTE)
    null_mask = singular <= tol
    projection = np.sqrt(np.sum(v[:, null_mask] ** 2, axis=1))
    not_ident = projection > NULL_COMPONENT
    keep = ~null_mask
    vk, sk = v[:, keep], singular[keep]
    cov_z = s2 * (vk / sk**2) @ vk.T
    cov = cov_z * np.outer(ranges, ranges)
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    se[not_ident] = np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = cov / np.outer(se, se)
    corr = np.clip(corr, -1.0, 1.0)
    corr[not_ident, :] = np.nan
    corr[:, not_ident] = np.nan
    for i in range(n):
        if not not_ident[i]:
            corr[i, i] = 1.0
    null = [(float(singular[k]), v[:, k]) for k in np.flatnonzero(null_mask)]
    verdicts: list[str] = []
    reasons: list[str] = []
    names = [p.path for p in params]
    for i, p in enumerate(params):
        if not_ident[i]:
            partners = sorted(
                {
                    names[j]
                    for _, vec in null
                    if abs(vec[i]) > NULL_COMPONENT
                    for j in range(n)
                    if j != i and abs(vec[j]) > NULL_COMPONENT
                }
            )
            verdicts.append("not_identifiable")
            if partners:
                reasons.append(
                    "The measurements see it only in a combination with "
                    + ", ".join(partners)
                    + ": changing them together in the proportions of the null direction "
                    "leaves every predicted value unchanged (to first order, within the "
                    "numerical accuracy)."
                )
            else:
                reasons.append(
                    "No measured value responds to it at these operating points (within the "
                    "numerical accuracy, or by less than "
                    f"{NULL_ABSOLUTE:g} of the uncertainties over its whole bound range)."
                )
            continue
        frac = float(se[i] / p.range)
        others = [
            (abs(float(corr[i, j])), j) for j in range(n) if j != i and not np.isnan(corr[i, j])
        ]
        c_abs, c_j = max(others) if others else (0.0, -1)
        text = (
            f"standard error {se[i]:.3g}{_unit_suffix(p.unit)} "
            f"({100 * frac:.3g} % of the bound range)"
        )
        weak = []
        if frac > WEAK_SE_FRACTION:
            weak.append(text + f", above {100 * WEAK_SE_FRACTION:g} %")
        if c_abs > WEAK_CORRELATION:
            weak.append(
                f"correlation {float(corr[i, c_j]):+.4f} with {names[c_j]}: the data mostly "
                "determine a combination of the two"
            )
        if weak:
            verdicts.append("weak")
            reasons.append(_sentence("; ".join(weak)))
        else:
            verdicts.append("identifiable")
            reasons.append(_sentence(text))
    return _Analysis(se, corr, singular, null, not_ident, verdicts, reasons, v, null_mask)


def _unit_suffix(unit: str) -> str:
    return "" if unit in ("", "1") else f" {unit}"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


def _null_directions(
    null: Sequence[tuple[float, np.ndarray]], names: Sequence[str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s, vec in null:
        k = int(np.argmax(np.abs(vec)))
        sign = 1.0 if vec[k] >= 0 else -1.0
        combination = {
            names[i]: round(float(sign * vec[i]), 4)
            for i in range(len(names))
            if abs(vec[i]) > NULL_COMPONENT
        }
        out.append({"singular_value": _num(s), "combination": combination})
    return out


def _condition(singular: np.ndarray) -> float | None:
    if singular.size == 0 or singular.max() == 0:
        return None
    smin = float(singular.min())
    return None if smin == 0 else float(singular.max()) / smin


# ----------------------------------------------------------------------------------------
# calibrate
# ----------------------------------------------------------------------------------------
def _measurement_set(measurements: Any) -> MeasurementSet:
    if isinstance(measurements, MeasurementSet):
        return measurements
    if isinstance(measurements, (str, os.PathLike)):
        return load_measurements(measurements)
    return MeasurementSet.from_dict(measurements)


def _check_method(method: str) -> None:
    if method not in METHODS:
        raise InvalidValueError(
            f"method must be one of {', '.join(METHODS)} (bounded least squares), got {method!r}."
        )


def _parse_step(step: Any) -> float | None:
    if step is None:
        return None
    value = parse_duration(step, "step")
    if value <= 0:
        raise InvalidValueError(f"step must be positive, got {step!r}.")
    return value


def _preflight(system: System) -> None:
    issues = system.check()
    if any(i.severity == "error" for i in issues):
        raise SystemCheckError(issues)


def _starting_failure(model: _Model, x: np.ndarray, exc: Exception) -> CalibrationError:
    shown = ", ".join(f"{p} = {v:.6g}" for p, v in model.values_at(x).items())
    return CalibrationError(
        f"The model cannot be evaluated at the starting values ({shown}): {exc} Start from "
        "values at which the system solves (System.set_values) or narrow the bounds."
    )


def calibrate(
    system: System,
    measurements: Any,
    parameters: Any,
    *,
    method: str = "trf",
    apply: bool = False,
    step: Any = None,
) -> CalibrationResult:
    """Fit parameters or inputs to measurements (design 14.2).

    Minimises the sum of squared uncertainty-weighted residuals ``(model - measured) /
    sigma`` with ``scipy.optimize.least_squares`` within the bounds, starting from the
    current values (clipped into the bounds). Steady points apply their settings to the
    starting system and solve; timed points run one simulation from the starting state,
    with each point's settings applied as an event at its time.

    Args:
        system: The system; it is left exactly as it was unless ``apply`` is true.
        measurements: A :class:`~worldparts.MeasurementSet`, its plain-dict form, or the
            path of a YAML, JSON or CSV file (:func:`~worldparts.load_measurements`).
        parameters: The paths to fit with their bounds: ``[{"path": "pump.wear_head",
            "lower": 0, "upper": 0.5}]``, ``{"pump.wear_head": [0, 0.5]}`` or plain paths.
            Missing bounds are the hard limits of the manifest. Number parameters and
            inputs only; bounds may carry units (``"0.1 bar"``).
        method: ``"trf"`` (default) or ``"dogbox"``.
        apply: Leave the system at the fitted values (set explicitly) instead of
            restoring it.
        step: Simulation step for timed points. By default it is chosen for accuracy
            (starting from the median interval between measurement times, at most the
            document's ``simulation.step``) and the predictions are extrapolated to zero
            step; a given step is used as ``System.simulate`` uses it, and checked.

    Returns:
        A :class:`CalibrationResult`.

    Raises:
        MeasurementError: The measurements are malformed, empty or do not fit the system
            (including settings of an input that a control writes).
        UnknownVariableError: A parameter path does not exist.
        InvalidValueError: A parameter that cannot be calibrated (a table, string or
            state, or an input written by a control), bad bounds, a bad method, or a step
            that would make the simulation of the timed points too long.
        SystemCheckError: The system has error-level check issues.
        CalibrationError: The model cannot be evaluated at the starting values.
    """
    _check_method(method)
    ms = _measurement_set(measurements)
    if ms.n_values == 0:
        raise MeasurementError(
            "The measurement set has no measured values; add points with 'measured', e.g. "
            "{points: [{name: p1, measured: {pump.volume_flow: '20 m3/h'}}]}."
        )
    resolved = ms.resolve(system)
    params = _resolve_parameters(system, parameters, resolved.points)
    step_s = _parse_step(step)
    _preflight(system)
    rows = list(resolved.values)
    keys = [(r.point, r.path) for r in rows]
    measured = np.array([r.value for r in rows])
    sigma = np.array([r.sigma for r in rows])
    wanted: dict[int, list[str]] = {}
    for r in rows:
        wanted.setdefault(r.point, []).append(r.path)
    model = _Model(system, params, resolved.points, wanted, step_s)
    obj = _Objective(model, keys)
    names = [p.path for p in params]
    initial = np.array([min(max(p.current, p.lower), p.upper) for p in params])
    z0 = (initial - obj.lower) / obj.range
    x_scale = obj.scales(z0)

    def fun(z: np.ndarray) -> np.ndarray:
        try:
            p = obj.predict(z)
        except _ModelFailure:
            return np.full(len(rows), np.nan)
        return (p - measured) / sigma

    def jac(z: np.ndarray) -> np.ndarray:
        try:
            p = obj.predict(z)
        except _ModelFailure:  # not expected: the optimiser asks at accepted points
            return np.zeros((len(rows), len(params)))
        return obj.jacobian_forward(z, p) / sigma[:, None]

    def fit(z_from: np.ndarray) -> Any:
        start = _start_point(obj, z_from, fun)
        return least_squares(fun, start, jac=jac, bounds=(0.0, 1.0), method=method, x_scale=x_scale)

    def at_start() -> tuple[np.ndarray, np.ndarray]:
        try:
            p = obj.predict(z0)
        except _ModelFailure as exc:
            raise _starting_failure(model, initial, exc) from None
        return p, (p - measured) / sigma

    step_change: float | None = None
    try:
        if model.extrapolate:
            _choose_step(obj, z0, sigma)
        p0, f0 = at_start()
        undefined = [keys[k] for k in np.flatnonzero(~np.isfinite(p0))]
        if undefined:
            i, path = undefined[0]
            raise CalibrationError(
                f"{path} is undefined (null) at point '{resolved.points[i].name}' with the "
                "starting values, so it cannot be compared with its measurement. Remove it "
                "from that point or start from values at which it is defined."
            )
        sol = fit(z0)
        z, p, f, worse = _outcome(obj, sol, z0, p0, f0, measured, sigma)
        # The step must also be accurate at the fitted values; refit with a finer one if not.
        for refit in range(MAX_STEP_REFITS + 1):
            if not model.can_check():
                step_change = None
                break
            try:
                step_change = _step_change(obj, z, sigma)
            except _ModelFailure:
                step_change = math.inf
            if step_change <= STEP_TOLERANCE or refit == MAX_STEP_REFITS:
                break
            if not model.can_refine():
                break
            model.step /= 2
            p0, f0 = at_start()
            sol = fit(z)
            z, p, f, worse = _outcome(obj, sol, z0, p0, f0, measured, sigma)
        jac_raw, err_raw, edges = obj.jacobian_accurate(z, p)
        jz = jac_raw / sigma[:, None]
        ez = err_raw / sigma[:, None]
        z, p, f, blind = _keep_blind_at_start(obj, z, p, f, z0, jz, ez, measured, sigma)
    finally:
        model.reset()
    x = obj.x(z)
    chi_square = float(f @ f)
    # Degrees of freedom: measured values minus the parameter combinations the data
    # determine (the rank of the Jacobian), so a null direction does not count as fitted.
    rank = len(params) - len(_analyse(jz, ez, params, 1.0).null)
    dof = len(rows) - rank
    reduced = chi_square / dof if dof > 0 else None
    p_value = float(chi2_distribution.sf(chi_square, dof)) if dof > 0 else None
    s2 = max(1.0, reduced) if reduced is not None else 1.0
    an = _analyse(jz, ez, params, s2)
    push = an.gauss_newton_step(jz, f)
    # For judging a push onto a bound, the error scale of the unconstrained fit: the misfit
    # that the bound itself causes must not inflate the standard error it is compared with.
    chi_free = max(0.0, chi_square - float(np.sum((jz @ push) ** 2)))
    s2_free = max(1.0, chi_free / dof) if dof > 0 else 1.0
    estimates: dict[str, ParameterEstimate] = {}
    notes: list[str] = []
    for i, prm in enumerate(params):
        se = None if an.not_identifiable[i] else float(an.se[i])
        at_bound = None if i in blind else _pushed_bound(z[i], push[i], se, prm.range)
        value = float(x[i])
        if at_bound is not None:
            value = prm.lower if at_bound == "lower" else prm.upper
            se_free = None if se is None else se * math.sqrt(s2_free / s2)
            notes.append(_bound_note(prm, at_bound, value, push[i], se_free))
        elif edges[i] and i not in blind:
            at_bound = "model_limit"
            side = "above" if edges[i] > 0 else "below"
            why = model.last_failure.rstrip(".")
            notes.append(
                f"{prm.path} ended at {value:.6g}{_unit_suffix(prm.unit)}, next to values "
                f"{side} it where the model cannot be evaluated ({why}). The data push it "
                "into that region, which suggests a wrong hypothesis or a missing parameter; "
                "its standard error is the local, one-sided one."
            )
        estimates[prm.path] = ParameterEstimate(
            prm.path,
            value,
            prm.unit,
            prm.lower,
            prm.upper,
            se,
            an.verdicts[i],
            an.reasons[i],
            initial=prm.current,
            at_bound=at_bound,
            reference=prm.reference,
        )
    residuals = [
        Residual(
            resolved.points[r.point].name,
            r.path,
            r.value,
            None if not np.isfinite(p[k]) else float(p[k]),
            r.sigma,
            r.unit,
            resolved.points[r.point].time,
            r.reference,
            r.sigma_default,
        )
        for k, r in enumerate(rows)
    ]
    success = bool(sol.success) and worse is None
    message = str(sol.message)
    lead: list[str] = []
    if worse is not None:
        x_end, chi_end = worse
        shown = ", ".join(f"{n} = {v:.6g}" for n, v in zip(names, x_end, strict=True))
        message += " The end point was worse than the start, so the start is reported."
        lead.append(
            f"The optimiser ended at a worse fit than the starting values (chi-square "
            f"{chi_end:.4g} at {shown}, against {float(f0 @ f0):.4g} at the start), so the "
            "reported values are the starting values, not a fit, and success is False. Start "
            "from other values (System.set_values), narrow the bounds or try "
            "method='dogbox'."
        )
    elif not sol.success:
        lead.append(f"The optimiser did not converge: {sol.message}")
    notes = (
        lead
        + _fit_notes(rows, len(params), reduced, p_value, dof, s2, model)
        + _step_notes(model, step_change)
        + notes
    )
    notes += _verdict_notes(estimates)
    applied = False
    if apply:
        system.set_values({e.path: e.value for e in estimates.values()})
        applied = True
    return CalibrationResult(
        parameters=estimates,
        residuals=residuals,
        paths=_path_fits(residuals),
        chi_square=chi_square,
        initial_chi_square=float(f0 @ f0),
        dof=dof,
        reduced_chi_square=reduced,
        p_value=p_value,
        error_scale=math.sqrt(s2),
        correlation=_corr_dict(names, an.corr),
        singular_values=[float(s) for s in an.singular],
        condition_number=_condition(an.singular),
        null_directions=_null_directions(an.null, names),
        success=success,
        message=message,
        method=method,
        evaluations=model.evaluations,
        failures=model.failures,
        failure_examples=list(model.failure_examples),
        applied=applied,
        step=model.step if model.timed else None,
        step_extrapolated=model.extrapolate,
        step_change=step_change,
        notes=notes,
    )


def _start_point(obj: _Objective, z: np.ndarray, fun: Any) -> np.ndarray:
    """The optimiser's start: ``z`` moved at least :data:`START_MARGIN` of each bound range
    inside the bounds, but by no more than the parameter's scale (the trust-region
    reflective method crawls away from a start on a bound), or ``z`` itself if the model
    fails there. The optimum does not depend on it; a Kv of 4 with the hard limits [1e-4,
    1e5] is not moved to 1000."""
    margin = np.minimum(START_MARGIN, obj.scales(z))
    start = np.clip(z, margin, 1.0 - margin)
    if np.array_equal(start, z) or not np.all(np.isfinite(fun(start))):
        return np.array(z, dtype=float)
    return start


def _outcome(
    obj: _Objective,
    sol: Any,
    z0: np.ndarray,
    p0: np.ndarray,
    f0: np.ndarray,
    measured: np.ndarray,
    sigma: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[np.ndarray, float] | None]:
    """The optimiser's end point with its predictions and residuals, or the start when the
    end point fits worse (or cannot be evaluated): then also the end point's values and
    chi-square, for the report."""
    z = np.clip(sol.x, 0.0, 1.0)
    try:
        p = obj.predict(z)
        f = (p - measured) / sigma
        chi = float(f @ f) if np.all(np.isfinite(f)) else math.inf
    except _ModelFailure:
        chi = math.inf
    if chi > float(f0 @ f0):
        return z0, p0, f0, (obj.x(z), chi)
    return z, p, f, None


def _pushed_bound(z: float, push: float, se: float | None, rng: float) -> str | None:
    """``lower`` or ``upper`` when the value sits on that bound and the unconstrained step
    ``push`` (bound-range units) points outwards by more than numerical noise (the larger
    of :data:`AT_BOUND` and 1e-3 of the standard error)."""
    noise = AT_BOUND if se is None else max(AT_BOUND, 1e-3 * se / rng)
    if z <= AT_BOUND and push < -noise:
        return "lower"
    if z >= 1.0 - AT_BOUND and push > noise:
        return "upper"
    return None


def _bound_note(prm: _Param, side: str, value: float, push: float, se: float | None) -> str:
    """The note for a parameter pushed onto a bound; ``push`` is the unconstrained step in
    bound-range units and ``se`` the standard error with the error scale of the
    unconstrained fit."""
    unit = _unit_suffix(prm.unit)
    beyond = abs(push) * prm.range
    if se is not None and se > 0:
        how = (
            f"about {beyond:.3g}{unit} beyond it ({beyond / se:.2g} standard errors of that "
            "unconstrained fit). More than about 2 standard errors suggests a wrong "
            "hypothesis, a missing parameter or a bound that is too tight; less is consistent "
            "with a true value at or near the bound. Its standard error is the local, "
            "unconstrained one."
        )
    else:
        how = (
            f"about {beyond:.3g}{unit} beyond it, together with the parameters it is "
            "confounded with: the combination the data determine is held by the bound, which "
            "suggests a wrong hypothesis, a missing parameter or a bound that is too tight."
        )
    return (
        f"{prm.path} ended at its {side} bound ({value:g}{unit}): the data push it further; "
        f"without the bound the fit would put it {how}"
    )


def _keep_blind_at_start(
    obj: _Objective,
    z: np.ndarray,
    p: np.ndarray,
    f: np.ndarray,
    z0: np.ndarray,
    jz: np.ndarray,
    ez: np.ndarray,
    measured: np.ndarray,
    sigma: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    """Put back to its starting value every parameter that no measured value responds to.

    A parameter is blind when its column of the normalised Jacobian ``jz`` is no larger
    than the null threshold of a single direction: :data:`NULL_RELATIVE` of the largest
    singular value, the column's own numerical error (from ``ez``), or
    :data:`NULL_ABSOLUTE`. The data say nothing about such a parameter, so the optimiser's
    start margin must not show up as an estimate. The change is kept only if the
    chi-square changes by no more than such columns allow.

    Returns:
        The point, its predictions and normalised residuals, and the indices of the blind
        parameters that are at their starting values (they were not fitted).
    """
    smax = float(np.linalg.norm(jz, 2)) if jz.size else 0.0
    col = np.linalg.norm(jz, axis=0)
    col_err = np.linalg.norm(ez, axis=0)
    tol = np.maximum(np.maximum(NULL_RELATIVE * smax, col_err), NULL_ABSOLUTE)
    blind = [i for i in range(len(z)) if col[i] <= tol[i]]
    moved = [i for i in blind if z[i] != z0[i]]
    if not moved:
        return z, p, f, blind
    zb = np.array(z, dtype=float)
    zb[moved] = z0[moved]
    try:
        pb = obj.predict(zb)
    except _ModelFailure:
        return z, p, f, [i for i in blind if i not in moved]
    fb = (pb - measured) / sigma
    chi, chi_b = float(f @ f), float(fb @ fb)
    # |change of f| <= sum over the reset columns of (norm + error) * |dz|, to first order.
    delta = float(np.sum((col + col_err)[moved] * np.abs(z - z0)[moved]))
    allowed = 2.0 * (2.0 * math.sqrt(chi) * delta + delta**2) + 1e-9 * max(1.0, chi)
    if np.all(np.isfinite(fb)) and chi_b - chi <= allowed:
        return zb, pb, fb, blind
    return z, p, f, [i for i in blind if i not in moved]


def _verdict_notes(estimates: Mapping[str, ParameterEstimate]) -> list[str]:
    notes: list[str] = []
    lost = [e.path for e in estimates.values() if e.verdict == "not_identifiable"]
    weak = [e.path for e in estimates.values() if e.verdict == "weak"]
    if lost:
        notes.append(
            f"Not identifiable: {', '.join(lost)}. The data are fitted equally well by other "
            "values (see null_directions), so the reported value is not an estimate; fix it "
            "at a known value, or add sensors or operating points (identifiability())."
        )
    if weak:
        notes.append(
            f"Weakly determined: {', '.join(weak)}; read the value with its standard error "
            "and correlations."
        )
    return notes


def _fit_notes(
    rows: Sequence[Any],
    n_params: int,
    reduced: float | None,
    p_value: float | None,
    dof: int,
    s2: float,
    model: _Model,
) -> list[str]:
    notes: list[str] = []
    defaults = sum(1 for r in rows if r.sigma_default)
    if defaults:
        notes.append(
            f"{defaults} of {len(rows)} measured values use the default uncertainty (1 % of "
            "the value, floored per kind of quantity); give 'sigma' where the instrument "
            "accuracy is known, since the standard errors and the chi-square depend on it."
        )
    if len(rows) < n_params:
        notes.append(
            f"There are fewer measured values ({len(rows)}) than parameters ({n_params}), "
            "so some parameters cannot be identified."
        )
    if dof <= 0:
        notes.append(
            "There are no more measured values than determined parameter combinations: the "
            "fit can pass through every value, so the reduced chi-square is undefined and "
            "the standard errors rest on the stated uncertainties alone."
        )
    elif reduced is not None and p_value is not None:
        if s2 > 1.0:
            if p_value < 0.01:
                strength = "the model misses the data by more than the uncertainties explain"
            elif p_value < 0.05:
                strength = "somewhat more misfit than the stated uncertainties explain"
            else:
                strength = "within what noise can explain"
            notes.append(
                f"Reduced chi-square {reduced:.3g} (p = {p_value:.2g}): {strength}. The "
                f"standard errors are scaled by sqrt({reduced:.3g}) = {math.sqrt(s2):.3g}."
                + (
                    " Check the hypothesis (a missing fault or parameter) and the sigmas."
                    if p_value < 0.01
                    else ""
                )
            )
        elif p_value > 0.99:
            notes.append(
                f"Reduced chi-square {reduced:.3g} (p = {p_value:.2g}): the residuals are "
                "smaller than the stated uncertainties would produce, so the uncertainties "
                "are probably overstated and the standard errors are conservative."
            )
    if model.failures:
        notes.append(
            f"{model.failures} model evaluation(s) failed during the fit; the optimiser "
            "stepped back from those values. First: " + model.failure_examples[0]
        )
    return notes


def _path_fits(residuals: Sequence[Residual]) -> dict[str, PathFit]:
    groups: dict[str, list[Residual]] = {}
    for r in residuals:
        groups.setdefault(r.path, []).append(r)
    out: dict[str, PathFit] = {}
    for path, rs in groups.items():
        res = np.array([np.nan if r.residual is None else r.residual for r in rs])
        nrm = np.array([np.nan if r.normalised is None else r.normalised for r in rs])
        out[path] = PathFit(
            path,
            rs[0].unit,
            len(rs),
            float(np.sqrt(np.mean(res**2))),
            float(np.sqrt(np.mean(nrm**2))),
            float(np.max(np.abs(nrm))),
        )
    return out


# ----------------------------------------------------------------------------------------
# identifiability
# ----------------------------------------------------------------------------------------
_POINT_KEYS = {"name", "time", "settings", "measured"}


def _operating_points(system: System, points: Any) -> tuple[ResolvedPoint, ...]:
    if points is None:
        return (ResolvedPoint("current", None, {}),)
    if isinstance(points, MeasurementSet):
        ms = points
    elif isinstance(points, (str, os.PathLike)):
        ms = load_measurements(points)
    else:
        if isinstance(points, Mapping) and "points" in points:
            points = points["points"]
        if isinstance(points, Mapping) or not isinstance(points, Iterable):
            raise InvalidValueError(
                "points must be a list of operating points, each a settings mapping such as "
                "{'valve.opening': 0.5} or {'name': ..., 'settings': {...}, 'time'?: ...}."
            )
        raw: list[Any] = []
        for i, p in enumerate(points):
            if not isinstance(p, Mapping):
                raise InvalidValueError(
                    f"points[{i}]: expected a settings mapping or a point, got {p!r}."
                )
            raw.append(dict(p) if set(p) <= _POINT_KEYS else {"settings": dict(p)})
        ms = MeasurementSet.from_dict({"points": raw})
    if not ms.points:
        raise InvalidValueError("points is empty; omit it to use the current operating point.")
    problems: list[str] = []
    names = [pt.name for pt in ms.points]
    problems += [
        f"The point name '{n}' is used {names.count(n)} times; names must be unique."
        for n in dict.fromkeys(names)
        if names.count(n) > 1
    ]
    problems += [
        f"point '{pt.name}': time must be finite and not negative, got {pt.time!r}."
        for pt in ms.points
        if pt.time is not None and not (math.isfinite(pt.time) and pt.time >= 0)
    ]
    resolved = [
        ResolvedPoint(
            pt.name,
            pt.time,
            parse_settings(system, pt.settings, f"point '{pt.name}'", problems, pt.time),
        )
        for pt in ms.points
    ]
    problems += _time_conflicts(resolved)
    if problems:
        raise MeasurementError("The operating points do not fit the system:", problems)
    return tuple(resolved)


def _sensor_entries(sensors: Any) -> list[tuple[str, Any]]:
    if isinstance(sensors, str):
        return [(sensors, None)]
    if isinstance(sensors, Mapping):
        return [(str(k), v) for k, v in sensors.items()]
    if isinstance(sensors, Iterable):
        return [(str(s), None) for s in sensors]
    raise InvalidValueError(
        "sensors must be a list of measured paths, e.g. ['pump.outlet.p', 'pump.volume_flow'], "
        "or a mapping of path to sigma."
    )


def _default_candidates(system: System) -> list[str]:
    """Observables, states and port pressures and temperatures: what a sensor could read.

    Observables that the manifest marks ``measurable: false`` (model quantities such as a
    pump's best-efficiency flow) are left out."""
    out: list[str] = []
    for v in system.variables():
        if not v.reported or v.type not in ("number", "integer") or v.unit is None:
            continue
        if v.kind == "observable":
            inst, name = v.path.split(".", 1)
            spec = system.manifest(inst).observables.get(name)
            if spec is not None and not spec.measurable:
                continue
        if v.kind in ("observable", "state") or (
            v.kind == "port" and v.path.rsplit(".", 1)[-1] in ("p", "T")
        ):
            out.append(v.path)
    return out


def identifiability(
    system: System,
    sensors: Any,
    parameters: Any,
    points: Any = None,
    *,
    candidates: Iterable[str] | None = None,
    step: Any = None,
) -> IdentifiabilityReport:
    """Which parameters can these sensors determine, before any data exists (design 14.2)?

    The sensors' sensitivity to the parameters is computed at the current parameter values
    and the given operating points, and analysed as in :func:`calibrate` with default
    uncertainties (1 % of each predicted value, floored per kind of quantity) and an error
    scale of 1: the standard errors a fit to such data would have if the model were right.
    Each candidate sensor is then added in turn, and the one that most reduces the standard
    error of the worst-determined parameter (relative to its bound range; a
    non-identifiable parameter is the worst) is recommended.

    Args:
        system: The system at the assumed true parameter values; left unchanged.
        sensors: Paths that will be measured at every point, or a mapping of path to its
            sigma (a number in the path's unit or a string with a unit; None for the
            default).
        parameters: As in :func:`calibrate`; the current values must lie within the bounds.
        points: Operating points: None for the current one, a list of settings mappings
            (``[{"valve.opening": 1}, {"valve.opening": 0.5}]``) or of points (``{"name",
            "settings", "time"}``), a :class:`~worldparts.MeasurementSet` or a file path.
        candidates: Paths to consider as an extra sensor (default: every reported
            observable, state and port pressure and temperature not already a sensor).
        step: Simulation step for timed points, as in :func:`calibrate`.

    Returns:
        An :class:`IdentifiabilityReport`.

    Raises:
        MeasurementError: Invalid operating points.
        UnknownVariableError: An unknown parameter path.
        InvalidValueError: An unknown or non-numeric sensor or candidate path, bad bounds,
            or a current value outside the bounds.
        SystemCheckError: The system has error-level check issues.
        CalibrationError: The model cannot be evaluated at the current values, or a
            sensor is undefined there.
    """
    pts = _operating_points(system, points)
    params = _resolve_parameters(system, parameters, pts)
    for prm in params:
        if not prm.lower <= prm.current <= prm.upper:
            raise InvalidValueError(
                f"{prm.path} = {prm.current:g}{_unit_suffix(prm.unit)} is outside its bounds "
                f"[{prm.lower:g}, {prm.upper:g}]; identifiability is evaluated at the current "
                "values, so set it inside the bounds or widen them."
            )
    measurable = measurable_paths(system)
    entries = _sensor_entries(sensors)
    if not entries:
        raise InvalidValueError("sensors is empty; name at least one measured path.")
    sensor_paths: list[str] = []
    given_sigma: dict[str, float] = {}
    for path, raw_sigma in entries:
        if path not in measurable:
            raise InvalidValueError(
                f"Sensor '{path}' is not a measurable (reported numeric) variable of system "
                f"'{system.name}'. " + format_choices(path, measurable)
            )
        if path in sensor_paths:
            raise InvalidValueError(f"Sensor '{path}' is listed twice.")
        sensor_paths.append(path)
        if raw_sigma is not None:
            where = f"sensors['{path}']"
            _, given_sigma[path], _ = resolve_value(
                MeasuredValue(0.0, None, *_sigma_parts(raw_sigma, path)), measurable[path], where
            )
    if candidates is None:
        cands = _default_candidates(system)
    else:
        cands = [candidates] if isinstance(candidates, str) else [str(c) for c in candidates]
        for c in cands:
            if c not in measurable:
                raise InvalidValueError(
                    f"Candidate sensor '{c}' is not a measurable (reported numeric) variable "
                    f"of system '{system.name}'. " + format_choices(c, measurable)
                )
    cands = [c for c in dict.fromkeys(cands) if c not in sensor_paths]
    step_s = _parse_step(step)
    _preflight(system)
    all_paths = sensor_paths + cands
    wanted = {i: all_paths for i in range(len(pts))}
    model = _Model(system, params, pts, wanted, step_s)
    keys = [(i, path) for i in range(len(pts)) for path in all_paths]
    is_sensor = np.array([path in sensor_paths for _, path in keys])
    obj = _Objective(model, keys, is_sensor)
    x0 = np.array([p.current for p in params])
    z0 = (x0 - obj.lower) / obj.range

    def sigmas(p0: np.ndarray, paths: Iterable[str]) -> np.ndarray:
        """Given or default sigmas of the rows of ``paths`` (1 elsewhere); a candidate
        without a default uncertainty is dropped from ``usable``."""
        wanted_paths = set(paths)
        out = np.ones(len(keys))
        for k, (_, path) in enumerate(keys):
            if path not in wanted_paths:
                continue
            info = measurable[path]
            if path in given_sigma:
                out[k] = given_sigma[path]
                continue
            try:
                out[k] = default_sigma(float(p0[k]), info.unit or "1", info.pressure_reference)
            except InvalidValueError:
                if path in sensor_paths:
                    raise
                usable.discard(path)  # a candidate of a kind with no default uncertainty
        return out

    usable: set[str] = set()
    step_change: float | None = None
    try:
        try:
            p0 = obj.predict(z0)
        except _ModelFailure as exc:
            raise _starting_failure(model, x0, exc) from None
        for k, (i, path) in enumerate(keys):
            if path in sensor_paths and not np.isfinite(p0[k]):
                raise CalibrationError(
                    f"Sensor {path} is undefined (null) at point '{pts[i].name}', so it "
                    "carries no information there. Choose another sensor or operating point."
                )
        if model.extrapolate:
            step_change = _choose_step(obj, z0, sigmas(p0, sensor_paths), is_sensor)
            try:
                p0 = obj.predict(z0)
            except _ModelFailure as exc:
                raise _starting_failure(model, x0, exc) from None
        jac_raw, err_raw, _ = obj.jacobian_accurate(z0, p0)
    finally:
        model.reset()
    finite = (
        np.isfinite(p0)
        & np.all(np.isfinite(jac_raw), axis=1)
        & np.all(np.isfinite(err_raw), axis=1)
    )
    usable = {c for c in cands if all(finite[k] for k, (_, path) in enumerate(keys) if path == c)}
    jac_raw = np.where(finite[:, None], jac_raw, 0.0)
    err_raw = np.where(finite[:, None], err_raw, 0.0)
    sigma = sigmas(p0, [*sensor_paths, *usable])
    jz = jac_raw / sigma[:, None]
    ez = err_raw / sigma[:, None]
    names = [p.path for p in params]
    ranges = obj.range

    def analyse(paths: set[str]) -> _Analysis:
        idx = [k for k, (_, path) in enumerate(keys) if path in paths]
        return _analyse(jz[idx], ez[idx], params, 1.0)

    base = analyse(set(sensor_paths))
    rel = base.relative(ranges)
    worst_i = int(np.argmax(rel)) if len(rel) else -1
    worst = names[worst_i] if worst_i >= 0 else None
    ranked: list[tuple[float, float, str, _Analysis]] = []
    for c in cands:
        if c not in usable:
            continue
        an = analyse({*sensor_paths, c})
        r = an.relative(ranges)
        ranked.append((float(r[worst_i]), float(np.sum(np.minimum(r, 10.0))), c, an))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    candidate_reports: list[SensorCandidate] = []
    for rel_w, _, c, an in ranked:
        equivalent = (
            tuple(other for rel_o, _, other, _ in ranked if other != c and _same(rel_o, rel_w))[
                :MAX_EQUIVALENT
            ]
            if math.isfinite(rel_w)
            else ()
        )
        se = None if an.not_identifiable[worst_i] else float(an.se[worst_i])
        candidate_reports.append(
            SensorCandidate(
                c,
                measurable[c].unit or "1",
                names[worst_i],
                params[worst_i].unit,
                se,
                None if se is None else se / ranges[worst_i],
                an.verdicts[worst_i],
                sum(1 for v in an.verdicts if v == "identifiable"),
                equivalent,
                sensor_reference=measurable[c].pressure_reference,
                parameter_reference=params[worst_i].reference,
            )
        )
    recommendation = None
    notes = [
        "Standard errors assume default uncertainties (1 % of each predicted value, floored "
        "per kind of quantity) unless sigmas were given, and a model that is right: they are "
        "the best a fit could do."
    ]
    if candidate_reports:
        best = candidate_reports[0]
        before = float(rel[worst_i])
        after = math.inf if best.relative_error is None else best.relative_error
        if after < before * (1 - 1e-6):
            recommendation = best
        else:
            notes.append(
                f"No single candidate sensor improves {worst}. Add operating points that "
                "change how it acts (other settings or times), or fix it at a known value."
            )
    elif cands:
        notes.append("No candidate sensor is defined at every operating point.")
    notes += _step_notes(model, step_change) if model.extrapolate else []
    estimates = {
        prm.path: ParameterEstimate(
            prm.path,
            prm.current,
            prm.unit,
            prm.lower,
            prm.upper,
            None if base.not_identifiable[i] else float(base.se[i]),
            base.verdicts[i],
            base.reasons[i],
            reference=prm.reference,
        )
        for i, prm in enumerate(params)
    }
    return IdentifiabilityReport(
        parameters=estimates,
        sensors=sensor_paths,
        points=[p.name for p in pts],
        correlation=_corr_dict(names, base.corr),
        singular_values=[float(s) for s in base.singular],
        condition_number=_condition(base.singular),
        null_directions=_null_directions(base.null, names),
        worst=worst,
        recommendation=recommendation,
        candidates=candidate_reports,
        step=model.step if model.timed else None,
        notes=notes,
    )


def _same(a: float, b: float) -> bool:
    if math.isinf(a) or math.isinf(b):
        return math.isinf(a) and math.isinf(b)
    return abs(a - b) <= 0.01 * max(abs(a), abs(b))


def _sigma_parts(raw: Any, path: str) -> tuple[float, str | None]:
    """``(sigma, unit)`` of a sensor's sigma given as a number or a string with a unit."""
    sigma, unit = _number_and_unit(raw, f"sensors['{path}']")
    if not sigma > 0:
        raise InvalidValueError(f"sensors['{path}']: sigma must be positive, got {raw!r}.")
    return sigma, unit
