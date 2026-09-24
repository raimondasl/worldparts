"""Fault diagnosis by calibrating hypotheses (design section 14.3).

:func:`diagnose` asks which fault best explains a set of measurements. The candidates are
the **fault modes** that component manifests declare (``faults``: a failure that shows as
one parameter or input moving within a plausible range, such as a pump's ``worn_impeller``
varying ``wear_head`` from 0 to 0.5) and, on request, a **leak** at any junction of the
system. Method, in short:

- **Baseline.** The system as given (no fault) is scored first: the chi-square of the
  uncertainty-weighted residuals of the measurements.
- **Hypotheses.** Each hypothesis (one fault, or a pair with ``max_faults=2``) is fitted to
  the measurements with :func:`~worldparts.calibrate` over the fault's range, starting from
  the system's values, so a fit is never worse than the baseline it contains. A leak
  hypothesis adds a ``leak`` component at the junction for the fit (its orifice diameter
  varied from the smallest leak the component represents up to the bore of the pipes that
  meet there) and removes it afterwards. A pair starts from the better of its two
  single-fault fits.
- **Score.** The Akaike information criterion on the weighted residuals, ``AIC =
  chi-square + 2 k`` with ``k`` the number of fitted fault parameters (``0`` for the
  baseline). Lower is better; hypotheses are ranked by it.
- **Support.** A hypothesis that adds faults to one that scores at least as well (for
  example a pair that adds a second fault to the best single fault, or any fault that does
  not beat the baseline it contains) adds a parameter that the data do not support (Arnold
  2010, "Uninformative parameters and model selection using Akaike's Information
  Criterion"): it is ranked but is not a separate explanation, so it is never the
  runner-up and gets no Akaike weight. Without this rule nearly every diagnosis would be
  "ambiguous" between a fault and the same fault plus a second one fitted to the noise.
- **Ambiguity.** The runner-up is the best-ranked supported hypothesis after the best; the
  diagnosis is ambiguous when it scores within :data:`AMBIGUITY_AIC` (2) of the best. The
  result lists the measurements that discriminate the best from the runner-up and, when
  ambiguous, the unmeasured variables whose predictions differ most between the two fits.

Nothing is applied to the system: every fit restores it, and added leaks are removed.
Everything is advisory: the standard errors are local, a fault that is not among the
hypotheses cannot be found (``unexplained`` flags a best hypothesis that still misses the
data), and the AIC differences assume that the stated uncertainties are right.
"""

from __future__ import annotations

import contextlib
import math
import time as _time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np
from scipy.stats import chi2 as chi2_distribution

from worldparts.calibration import (
    _MODEL_FAILURES,
    CalibrationResult,
    Residual,
    _choose_step,
    _default_candidates,
    _measurement_set,
    _Model,
    _ModelFailure,
    _num,
    _Objective,
    _Param,
    _parse_step,
    _preflight,
    _step_notes,
    _unit_suffix,
    calibrate,
)
from worldparts.errors import (
    CalibrationError,
    InvalidValueError,
    MeasurementError,
    UnknownComponentError,
    UnknownVariableError,
    format_choices,
)
from worldparts.measurements import ResolvedMeasurements, default_sigma, measurable_paths
from worldparts.system import System

__all__ = [
    "AMBIGUITY_AIC",
    "BASELINE",
    "CONCLUSIONS",
    "FALSE_ALARM_LEVEL",
    "LEAK_PREFIX",
    "MAX_FAULTS",
    "UNEXPLAINED_P",
    "DiagnosisResult",
    "Discriminator",
    "FaultEstimate",
    "Hypothesis",
    "SensorSuggestion",
    "diagnose",
]

#: Two hypotheses whose AIC differ by less than this are both plausible: the diagnosis is
#: ambiguous (design 14.3).
AMBIGUITY_AIC: float = 2.0
#: Name of the no-fault hypothesis: the system as given.
BASELINE: str = "baseline"
#: Prefix of a leak hypothesis, followed by a port path at the junction:
#: ``leak_at:pump.outlet``.
LEAK_PREFIX: str = "leak_at:"
#: The most simultaneous faults :func:`diagnose` combines (``max_faults``).
MAX_FAULTS: int = 2
#: The best hypothesis is ``unexplained`` when the chi-square p-value of its fit is below
#: this: it misses the data by more than the stated uncertainties explain.
UNEXPLAINED_P: float = 0.01
#: A fault is concluded only when the chance of so large an advantage over no fault on a
#: healthy plant (``false_alarm``) is at most this; above it the evidence is weak.
FALSE_ALARM_LEVEL: float = 0.05
#: Values of :attr:`DiagnosisResult.conclusion`, in order of precedence.
CONCLUSIONS: tuple[str, ...] = ("unexplained", "ambiguous", "no_fault", "weak_evidence", "fault")
#: At most this many discriminating measurements and sensor suggestions are listed.
MAX_LISTED: int = 10
#: Sensor suggestions separate the best and the runner-up by at least this many default
#: uncertainties at some operating point.
SUGGESTION_SEPARATION: float = 1.0
#: Measurements whose predictions under the best and the runner-up differ by less than this
#: many standard uncertainties are not listed as discriminating.
DISCRIMINATING_SEPARATION: float = 0.1

#: Catalogue alias of the component a leak hypothesis adds.
_LEAK_TYPE = "leak"
#: AIC values closer than this are ties (broken by fewer faults, then by order).
_TIE = 1e-6


# ----------------------------------------------------------------------------------------
# results
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FaultEstimate:
    """The fitted magnitude of one fault of a hypothesis.

    Every number is in ``unit`` (the declared unit of ``path``) except ``leak_flow``.

    Attributes:
        fault: Fault reference, ``<instance>.<fault>`` or ``leak_at:<port>``.
        path: The varied parameter or input (for a leak, the diameter of the leak
            component added for the fit, for example ``leak_at_pump_outlet.diameter``).
        value: Fitted value.
        unit: Declared unit of ``path``.
        standard_error: One standard deviation of the fit (local, linearised); None when
            the measurements do not determine it.
        lower: Lower end of the fault's range.
        upper: Upper end of the fault's range.
        healthy: The value without the fault (for a leak, the smallest leak the component
            represents, which is negligible).
        baseline: The value in the system as given (None for a leak: there is none).
        verdict: ``identifiable``, ``weak`` or ``not_identifiable`` (as in calibration).
        at_bound: ``lower``, ``upper`` or ``model_limit`` when the data push the fit onto
            the edge of the range (see :class:`~worldparts.ParameterEstimate`), else None.
        relative: True when the range was given as multiples of the baseline value.
        description: The fault's description from the manifest.
        reference: Pressure reference of ``path``, if any.
        leak_flow: For a leak, the leak's outflow in L/min at each steady operating point
            at the fitted diameter; None otherwise.
    """

    fault: str
    path: str
    value: float
    unit: str
    standard_error: float | None
    lower: float
    upper: float
    healthy: float
    baseline: float | None
    verdict: str
    at_bound: str | None
    relative: bool = False
    description: str = ""
    reference: str | None = None
    leak_flow: dict[str, float | None] | None = None

    @property
    def deviation(self) -> float:
        """``value - healthy``: the size of the fault, in ``unit``."""
        return self.value - self.healthy

    @property
    def factor(self) -> float | None:
        """``value / baseline`` for a relative fault (None otherwise, or with a zero
        baseline)."""
        if not self.relative or not self.baseline:
            return None
        return self.value / self.baseline

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; every number is in ``unit`` except ``leak_flow`` (L/min)."""
        out: dict[str, Any] = {
            "path": self.path,
            "value": _num(self.value),
            "unit": self.unit,
            "standard_error": _num(self.standard_error),
            "healthy": _num(self.healthy),
            "baseline": _num(self.baseline),
            "lower": _num(self.lower),
            "upper": _num(self.upper),
            "verdict": self.verdict,
        }
        if self.factor is not None:
            out["factor"] = _num(self.factor)
        if self.at_bound is not None:
            out["at_bound"] = self.at_bound
        if self.reference:
            out["reference"] = self.reference
        if self.leak_flow is not None:
            out["leak_flow"] = {
                "unit": "L/min",
                "points": {k: _num(v) for k, v in self.leak_flow.items()},
            }
        return out


@dataclass
class Hypothesis:
    """One explanation of the measurements, fitted and scored.

    Attributes:
        name: ``baseline``, a fault reference (``pump.worn_impeller``, ``leak_at:pump.outlet``)
            or a combination joined by ``" + "``.
        faults: The fault references (empty for the baseline).
        estimates: Fault reference to its :class:`FaultEstimate`.
        chi_square: Sum of squared normalised residuals at the fit.
        parameters: Number of fitted fault parameters ``k``.
        aic: ``chi_square + 2 * parameters``; lower is better.
        measurements: Number of measured values.
        dof: Degrees of freedom of the fit (measured values minus the parameter
            combinations the data determine).
        reduced_chi_square: ``chi_square / dof`` (None when ``dof <= 0``).
        p_value: Chi-square p-value of the fit (None when ``dof <= 0``): below about 0.01
            the hypothesis misses the data by more than the uncertainties explain.
        residuals: Every measured value with the prediction at the fit.
        success: False when the optimiser ended worse than its start (the start is then
            reported; see ``calibration``).
        rank: Position in the ranking, 1 for the best.
        delta_aic: AIC minus the best AIC.
        supported: False when the hypothesis adds faults to one that scores at least as
            well: the extra fault is not supported by the data, and the hypothesis is not
            a separate explanation (never the runner-up, no weight).
        weight: Akaike weight among the supported hypotheses, ``exp(-delta_aic / 2)``
            normalised to sum to 1 (None when not supported).
        notes: Remarks on this fit.
        calibration: The full :class:`~worldparts.CalibrationResult` of the fit (None for
            the baseline); not part of :meth:`to_dict`.
    """

    name: str
    faults: tuple[str, ...]
    estimates: dict[str, FaultEstimate]
    chi_square: float
    parameters: int
    aic: float
    measurements: int
    dof: int
    reduced_chi_square: float | None
    p_value: float | None
    residuals: list[Residual]
    success: bool = True
    rank: int = 0
    delta_aic: float = 0.0
    supported: bool = True
    weight: float | None = None
    notes: list[str] = field(default_factory=list)
    calibration: CalibrationResult | None = None

    def to_dict(self, residuals: bool = True) -> dict[str, Any]:
        """JSON-ready dict (without the full calibration result)."""
        out: dict[str, Any] = {
            "name": self.name,
            "rank": self.rank,
            "faults": list(self.faults),
            "estimates": {k: e.to_dict() for k, e in self.estimates.items()},
            "aic": _num(self.aic),
            "delta_aic": _num(self.delta_aic),
            "supported": self.supported,
            "weight": _num(self.weight),
            "chi_square": _num(self.chi_square),
            "parameters": self.parameters,
            "measurements": self.measurements,
            "dof": self.dof,
            "reduced_chi_square": _num(self.reduced_chi_square),
            "p_value": _num(self.p_value),
            "success": self.success,
        }
        if residuals:
            out["residuals"] = [r.to_dict() for r in self.residuals]
        if self.notes:
            out["notes"] = list(self.notes)
        return out


@dataclass(frozen=True)
class Discriminator:
    """A measured value compared under the best hypothesis and the runner-up.

    Attributes:
        point: Point name.
        path: Measured variable.
        unit: Its display unit (of ``measured``, ``sigma``, ``best`` and ``runner_up``).
        measured: The measured value.
        sigma: Its standard uncertainty.
        best: The value predicted by the best hypothesis's fit.
        runner_up: The value predicted by the runner-up's fit.
        separation: ``(runner_up - best) / sigma``: how many standard uncertainties apart
            the two predictions are.
        delta_chi_square: The runner-up's squared normalised residual minus the best's:
            positive values are evidence for the best.
        time: Time in s for a point of a time series, else None.
        reference: Pressure reference, if any.
    """

    point: str
    path: str
    unit: str
    measured: float
    sigma: float
    best: float
    runner_up: float
    separation: float
    delta_chi_square: float
    time: float | None = None
    reference: str | None = None

    @property
    def favours(self) -> str:
        """``best`` or ``runner_up``: the hypothesis this measurement agrees with more."""
        return "best" if self.delta_chi_square >= 0 else "runner_up"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; values are in ``unit``."""
        out: dict[str, Any] = {"point": self.point, "path": self.path}
        if self.time is not None:
            out["time"] = self.time
        out.update(
            {
                "measured": _num(self.measured),
                "sigma": _num(self.sigma),
                "best": _num(self.best),
                "runner_up": _num(self.runner_up),
                "unit": self.unit,
                "separation": _num(self.separation),
                "delta_chi_square": _num(self.delta_chi_square),
                "favours": self.favours,
            }
        )
        if self.reference:
            out["reference"] = self.reference
        return out


@dataclass(frozen=True)
class SensorSuggestion:
    """An unmeasured variable whose predictions differ between the best hypothesis and the
    runner-up: measuring it could resolve an ambiguous diagnosis.

    The predictions are those of the current fits. A refit that includes the new
    measurement can narrow the gap, so ``separation`` is what the sensor could show, not a
    promise.

    Attributes:
        path: The variable.
        unit: Its display unit (of ``best``, ``runner_up`` and ``sigma``).
        point: The operating point where the two predictions differ most.
        best: Predicted under the best hypothesis there.
        runner_up: Predicted under the runner-up there.
        sigma: The default uncertainty of such a measurement (1 % of the value, floored
            per kind of quantity).
        separation: ``|runner_up - best| / sigma``.
        reference: Pressure reference, if any.
    """

    path: str
    unit: str
    point: str
    best: float
    runner_up: float
    sigma: float
    separation: float
    reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; values are in ``unit``."""
        out: dict[str, Any] = {
            "path": self.path,
            "point": self.point,
            "best": _num(self.best),
            "runner_up": _num(self.runner_up),
            "sigma": _num(self.sigma),
            "unit": self.unit,
            "separation": _num(self.separation),
        }
        if self.reference:
            out["reference"] = self.reference
        return out


@dataclass
class DiagnosisResult:
    """The result of :func:`diagnose`.

    Attributes:
        conclusion: The verdict in one word, the first that applies: ``unexplained`` (even
            the best hypothesis misses the data), ``ambiguous`` (the runner-up is within
            :data:`AMBIGUITY_AIC`), ``no_fault`` (the baseline is best), ``weak_evidence`` (a
            fault is best, but ``false_alarm`` exceeds :data:`FALSE_ALARM_LEVEL`) or
            ``fault``.
        hypotheses: Every evaluated hypothesis, the baseline included, ranked by AIC (best
            first).
        best: Name of the best hypothesis.
        runner_up: Name of the best-ranked supported hypothesis after the best, or None.
        ambiguous: True when the runner-up scores within :data:`AMBIGUITY_AIC` of the
            best: the measurements cannot tell them apart.
        plausible: The supported hypotheses within :data:`AMBIGUITY_AIC` of the best, best
            first (just the best when not ambiguous).
        unexplained: True when even the best hypothesis misses the data by more than the
            stated uncertainties explain (its chi-square p-value is below
            :data:`UNEXPLAINED_P`): the fault may not be among the hypotheses, or the
            uncertainties are too small.
        false_alarm: When the best hypothesis is a fault: an upper bound on the probability
            that a healthy plant (with the model and the uncertainties right) would give one
            of the tested hypotheses at least this AIC advantage over no fault by chance
            (Bonferroni over the hypotheses that contain the baseline, each advantage
            ``chi-square drop - 2 k`` bounded by the chi-square distribution with ``k``
            degrees of freedom). Near 1 means that the evidence for a fault is weak; None
            when the best hypothesis is the baseline.
        discriminating: Measured values whose predictions differ between the best and the
            runner-up, most separated first.
        suggested_sensors: When ambiguous, unmeasured variables that the two fits predict
            differently, most separated first.
        skipped: Hypotheses that were not evaluated, with the reason (for example a fault
            whose input a control writes, or a model that fails at the starting values).
        notes: Plain-language conclusions and caveats for an agent.
        max_faults: The ``max_faults`` of the call.
        evaluations: Model evaluations over all fits.
        elapsed: Wall time in s.
        step: Simulation step of timed points in s for the baseline (None without timed
            points).
    """

    conclusion: str
    hypotheses: list[Hypothesis]
    best: str
    runner_up: str | None
    ambiguous: bool
    plausible: list[str]
    unexplained: bool
    false_alarm: float | None
    discriminating: list[Discriminator]
    suggested_sensors: list[SensorSuggestion]
    skipped: dict[str, str]
    notes: list[str]
    max_faults: int
    evaluations: int
    elapsed: float
    step: float | None = None

    def __getitem__(self, name: str) -> Hypothesis:
        """The hypothesis called ``name``."""
        for h in self.hypotheses:
            if h.name == name:
                return h
        raise UnknownVariableError(
            f"'{name}' is not an evaluated hypothesis. "
            + format_choices(name, [h.name for h in self.hypotheses])
        )

    @property
    def best_hypothesis(self) -> Hypothesis:
        """The best :class:`Hypothesis`."""
        return self.hypotheses[0]

    @property
    def ranking(self) -> list[str]:
        """Hypothesis names, best first."""
        return [h.name for h in self.hypotheses]

    def to_dict(self, max_hypotheses: int | None = None, residuals: bool = True) -> dict[str, Any]:
        """JSON-ready dict with at most ``max_hypotheses`` ranked hypotheses (the runner-up
        is always included); residuals of every listed hypothesis unless ``residuals`` is
        False."""
        shown = self.hypotheses if max_hypotheses is None else self.hypotheses[:max_hypotheses]
        if self.runner_up and all(h.name != self.runner_up for h in shown):
            shown = [*shown, self[self.runner_up]]
        return {
            "conclusion": self.conclusion,
            "best": self.best,
            "runner_up": self.runner_up,
            "ambiguous": self.ambiguous,
            "plausible": list(self.plausible),
            "unexplained": self.unexplained,
            "false_alarm": _num(self.false_alarm),
            "notes": list(self.notes),
            "hypotheses": [h.to_dict(residuals) for h in shown],
            "hypotheses_total": len(self.hypotheses),
            "discriminating": [d.to_dict() for d in self.discriminating],
            "suggested_sensors": [s.to_dict() for s in self.suggested_sensors],
            "skipped": dict(self.skipped),
            "max_faults": self.max_faults,
            "evaluations": self.evaluations,
            "elapsed": {"value": _num(self.elapsed), "unit": "s"},
            "step": None if self.step is None else {"value": _num(self.step), "unit": "s"},
        }


# ----------------------------------------------------------------------------------------
# fault references
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Fault:
    """A fault resolved against a system: what to vary, over which range."""

    ref: str
    path: str
    unit: str
    reference: str | None
    lower: float
    upper: float
    healthy: float
    baseline: float | None
    relative: bool
    description: str
    #: For a leak: the instance name of the added leak component and the port it joins.
    leak: tuple[str, str] | None = None

    @property
    def contains_baseline(self) -> bool:
        """Whether the fault's range contains the system as given (a leak's smallest
        diameter is a negligible leak), so the hypothesis includes the baseline."""
        if self.baseline is None:
            return True
        tol = 1e-12 * max(1.0, abs(self.lower), abs(self.upper))
        return self.lower - tol <= self.baseline <= self.upper + tol

    def bounds(self) -> dict[str, Any]:
        return {"path": self.path, "lower": self.lower, "upper": self.upper}


class _Faults:
    """The fault references of a system: component fault modes and junctions for leaks."""

    def __init__(self, system: System, points: Sequence[Any]) -> None:
        self.system = system
        self.points = points
        self.controlled = {c.actuate: name for name, c in system.controls.items()}
        self.modes: dict[str, tuple[str, Any]] = {}  # ref -> (instance, FaultSpec)
        for name in system.components:
            inst = system._instances.get(name)
            if inst is None:
                continue
            for fname, spec in inst.manifest.faults.items():
                self.modes[f"{name}.{fname}"] = (name, spec)
        system._build()
        groups: dict[int, list[str]] = {}
        for port, node in system._port_nodes.items():
            groups.setdefault(node.index, []).append(port)
        #: Junction ports (sorted) by node index, for nodes where two or more ports meet.
        self.junctions = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
        self.node_of = {p: k for k, ports in self.junctions.items() for p in ports}
        self._leak_names: set[str] = set()
        self._resolved: dict[str, tuple[_Fault, str | None]] = {}

    def valid_refs(self) -> list[str]:
        return [*self.modes, *(LEAK_PREFIX + ports[0] for ports in self.junctions.values())]

    def default_modes(self) -> list[str]:
        return list(self.modes)

    def default_leaks(self) -> list[str]:
        return [LEAK_PREFIX + ports[0] for ports in self.junctions.values()]

    def resolve(self, ref: str, where: str) -> tuple[_Fault, str | None]:
        """The fault for ``ref`` (the same object for the same reference), and the reason
        it cannot be evaluated here, or None.

        Raises:
            UnknownVariableError: ``ref`` names no fault mode or junction.
        """
        if ref not in self._resolved:
            self._resolved[ref] = self._resolve(ref, where)
        return self._resolved[ref]

    def _resolve(self, ref: str, where: str) -> tuple[_Fault, str | None]:
        if ref.startswith(LEAK_PREFIX):
            return self._leak(ref, where), None
        if ref not in self.modes:
            raise UnknownVariableError(self._unknown(ref, where))
        inst, spec = self.modes[ref]
        manifest = self.system.manifest(inst)
        var = manifest.parameters.get(spec.path) or manifest.inputs.get(spec.path)
        assert var is not None  # the manifest loader checked it
        path = f"{inst}.{spec.path}"
        current = float(self.system.get(path))
        lower, upper, healthy = spec.resolve(current)
        if var.minimum is not None:
            lower, healthy = max(lower, var.minimum), max(healthy, var.minimum)
        if var.maximum is not None:
            upper, healthy = min(upper, var.maximum), min(healthy, var.maximum)
        fault = _Fault(
            ref,
            path,
            var.unit or "1",
            var.reference,
            lower,
            upper,
            healthy,
            current,
            spec.relative,
            spec.description,
        )
        if path in self.controlled:
            return fault, (
                f"control '{self.controlled[path]}' writes {path}, so the fault would be "
                "overridden; remove the control to test it"
            )
        setters = [p.name for p in self.points if path in p.settings]
        if setters:
            shown = ", ".join(f"'{n}'" for n in setters[:3]) + (" ..." if len(setters) > 3 else "")
            return fault, (
                f"the measurement points ({shown}) set {path}, so it cannot be fitted as one value"
            )
        if not lower < upper:
            unit = _unit_suffix(fault.unit)
            return fault, (
                f"its range is empty: {path} is {current:g}{unit}, and the range "
                f"{spec.lower:g} to {spec.upper:g} times that is [{lower:g}, {upper:g}]{unit}"
                if spec.relative
                else f"its range [{lower:g}, {upper:g}] is empty within the hard limits"
            )
        return fault, None

    def _unknown(self, ref: str, where: str) -> str:
        valid = self.valid_refs()
        head = f"{where}: '{ref}' is not a fault of system '{self.system.name}'."
        inst, _, name = ref.partition(".")
        if inst in self.system.components and name:
            try:
                m = self.system.manifest(inst)
            except UnknownComponentError:
                m = None
            if m is not None and not m.faults:
                head += f" {inst} ({m.alias}) declares no fault modes."
            elif m is not None:
                head += f" Fault modes of {inst}: {', '.join(m.faults)}."
        return (
            f"{head} A hypothesis is '<instance>.<fault>', '{LEAK_PREFIX}<port>' at a junction, "
            "or a list of those for a combination. " + format_choices(ref, valid)
        )

    def _leak(self, ref: str, where: str) -> _Fault:
        port = ref[len(LEAK_PREFIX) :].strip()
        if port not in self.node_of:
            junctions = [LEAK_PREFIX + ports[0] for ports in self.junctions.values()]
            what = (
                "is not connected to another port"
                if port in self.system._port_nodes
                else "is not a port of the system"
            )
            raise UnknownVariableError(
                f"{where}: '{ref}': {port} {what}; a leak is tested at a junction, where two "
                "or more ports meet (name any of its ports). " + format_choices(ref, junctions)
            )
        try:
            leak_manifest = self.system.catalog.get(_LEAK_TYPE)
        except UnknownComponentError:
            raise InvalidValueError(
                f"{where}: '{ref}' needs the '{_LEAK_TYPE}' component, which the system's "
                "catalogue does not have."
            ) from None
        spec = leak_manifest.parameters["diameter"]
        lower = float(spec.minimum if spec.minimum is not None else 0.1)
        bores = [
            float(self.system.get(f"{p.split('.')[0]}.diameter", unit=spec.unit))
            for p in self.junctions[self.node_of[port]]
            if self.system.manifest(p.split(".")[0]).alias == "pipe"
        ]
        upper = max(bores) if bores else spec.maximum
        if upper is None:
            upper = 1000.0
        if spec.maximum is not None:
            upper = min(upper, spec.maximum)
        name = self._leak_name(port)
        return _Fault(
            ref,
            f"{name}.diameter",
            spec.unit or "mm",
            None,
            lower,
            float(upper),
            lower,
            None,
            False,
            "A leak at the junction: an orifice discharging to atmosphere, its equivalent "
            "diameter fitted (the discharge coefficient is the component's default).",
            (name, port),
        )

    def _leak_name(self, port: str) -> str:
        base = "leak_at_" + port.replace(".", "_")
        name, k = base, 1
        while name in self.system.components or name in self._leak_names:
            k += 1
            name = f"{base}_{k}"
        self._leak_names.add(name)
        return name

    def node(self, fault: _Fault) -> int | None:
        return None if fault.leak is None else self.node_of[fault.leak[1]]


def _hypothesis_entries(hypotheses: Any) -> list[tuple[list[str], str]]:
    """``(refs, where)`` from the accepted forms of ``hypotheses``."""
    if isinstance(hypotheses, str):
        hypotheses = [hypotheses]
    if isinstance(hypotheses, Mapping) or not isinstance(hypotheses, Sequence):
        raise InvalidValueError(
            "hypotheses must be a list of fault references, e.g. ['pump.worn_impeller', "
            "'leak_at:pump.outlet', ['pump.worn_impeller', 'filter.clogged']], got "
            f"{type(hypotheses).__name__}."
        )
    out: list[tuple[list[str], str]] = []
    for i, entry in enumerate(hypotheses):
        where = f"hypotheses[{i}]"
        if isinstance(entry, str):
            refs = [r.strip() for r in entry.split("+")]
        elif isinstance(entry, Sequence) and all(isinstance(r, str) for r in entry):
            refs = [r.strip() for r in entry]
        else:
            raise InvalidValueError(
                f"{where}: expected a fault reference such as 'pump.worn_impeller' or a list "
                f"of them for a combination, got {entry!r}."
            )
        if not refs or any(not r for r in refs):
            raise InvalidValueError(f"{where}: empty fault reference in {entry!r}.")
        out.append((refs, where))
    if not out:
        raise InvalidValueError(
            "hypotheses is empty; omit it to test every fault mode of the system's components."
        )
    return out


# ----------------------------------------------------------------------------------------
# evaluation
# ----------------------------------------------------------------------------------------
@contextlib.contextmanager
def _with_leaks(system: System, faults: Sequence[_Fault]) -> Iterator[None]:
    """Add a leak component for every leak fault (at its smallest diameter), remove them
    on exit."""
    added: list[str] = []
    try:
        for f in faults:
            if f.leak is None:
                continue
            name, port = f.leak
            system.add(name, _LEAK_TYPE, diameter=f.healthy, opening=1.0)
            added.append(name)
            system.connect(f"{name}.port", port)
        yield
    finally:
        for name in reversed(added):
            system.remove(name)


@contextlib.contextmanager
def _with_values(system: System, values: Mapping[str, float]) -> Iterator[None]:
    """Set ``values`` (a warm start), put everything back on exit."""
    if not values:
        yield
        return
    saved = system._save_values()
    try:
        system.set_values(dict(values))
        yield
    finally:
        system._restore_values(saved)


def _leak_flows(
    system: System, faults: Sequence[_Fault], values: Mapping[str, float], resolved: Any
) -> dict[str, dict[str, float | None]]:
    """Each leak's outflow in L/min at each steady point, at the fitted ``values``."""
    leaks = [f for f in faults if f.leak is not None]
    if not leaks:
        return {}
    out: dict[str, dict[str, float | None]] = {f.ref: {} for f in leaks}
    saved = system._save_values()
    try:
        for point in resolved.points:
            if point.time is not None:
                continue
            system._restore_values(saved)
            try:
                system.set_values({**values, **point.settings})
                result = system._solve([])
            except _MODEL_FAILURES:
                for f in leaks:
                    out[f.ref][point.name] = None
                continue
            for f in leaks:
                assert f.leak is not None
                out[f.ref][point.name] = result.get(f"{f.leak[0]}.volume_flow", unit="L/min")
    finally:
        system._restore_values(saved)
    return out


def _fit(
    system: System,
    ms: Any,
    faults: Sequence[_Fault],
    resolved: ResolvedMeasurements,
    step: float | None,
    start: Mapping[str, float],
) -> Hypothesis:
    """Calibrate the faults of one hypothesis; the system is left as it was.

    Raises:
        CalibrationError: The model cannot be evaluated at the starting values.
    """
    with _with_leaks(system, faults), _with_values(system, start):
        cal = calibrate(system, ms, [f.bounds() for f in faults], step=step)
        flows = _leak_flows(system, faults, cal.values, resolved)
    estimates: dict[str, FaultEstimate] = {}
    for f in faults:
        e = cal.parameters[f.path]
        estimates[f.ref] = FaultEstimate(
            fault=f.ref,
            path=f.path,
            value=e.value,
            unit=f.unit,
            standard_error=e.standard_error,
            lower=f.lower,
            upper=f.upper,
            healthy=f.healthy,
            baseline=f.baseline,
            verdict=e.verdict,
            at_bound=e.at_bound,
            relative=f.relative,
            description=f.description,
            reference=f.reference,
            leak_flow=flows.get(f.ref),
        )
    k = len(faults)
    return Hypothesis(
        name=" + ".join(f.ref for f in faults),
        faults=tuple(f.ref for f in faults),
        estimates=estimates,
        chi_square=cal.chi_square,
        parameters=k,
        aic=cal.chi_square + 2.0 * k,
        measurements=len(cal.residuals),
        dof=cal.dof,
        reduced_chi_square=cal.reduced_chi_square,
        p_value=cal.p_value,
        residuals=list(cal.residuals),
        success=cal.success,
        notes=[n for n in cal.notes if not _generic_note(n)],
        calibration=cal,
    )


def _generic_note(note: str) -> bool:
    """Calibration notes that concern every hypothesis alike (reported once)."""
    return "use the default uncertainty" in note


def _baseline(
    system: System, resolved: ResolvedMeasurements, step: float | None
) -> tuple[Hypothesis, _Model]:
    """The system as given, scored without fitting anything."""
    rows = list(resolved.values)
    keys = [(r.point, r.path) for r in rows]
    measured = np.array([r.value for r in rows])
    sigma = np.array([r.sigma for r in rows])
    wanted: dict[int, list[str]] = {}
    for r in rows:
        wanted.setdefault(r.point, []).append(r.path)
    model = _Model(system, [], resolved.points, wanted, step)
    obj = _Objective(model, keys)
    z = np.zeros(0)
    change = None
    try:
        if model.extrapolate:
            change = _choose_step(obj, z, sigma)
        p = obj.predict(z)
    except _ModelFailure as exc:
        raise CalibrationError(
            f"The system as given cannot be evaluated at the measured points: {exc} Diagnosis "
            "needs a model that solves without a fault; check the system (System.solve)."
        ) from None
    finally:
        model.reset()
    undefined = [keys[k] for k in np.flatnonzero(~np.isfinite(p))]
    if undefined:
        i, path = undefined[0]
        raise CalibrationError(
            f"{path} is undefined (null) at point '{resolved.points[i].name}' in the system as "
            "given, so it cannot be compared with its measurement. Remove it from that point."
        )
    f = (p - measured) / sigma
    chi = float(f @ f)
    n = len(rows)
    residuals = [
        Residual(
            resolved.points[r.point].name,
            r.path,
            r.value,
            float(p[k]),
            r.sigma,
            r.unit,
            resolved.points[r.point].time,
            r.reference,
            r.sigma_default,
        )
        for k, r in enumerate(rows)
    ]
    hyp = Hypothesis(
        name=BASELINE,
        faults=(),
        estimates={},
        chi_square=chi,
        parameters=0,
        aic=chi,
        measurements=n,
        dof=n,
        reduced_chi_square=chi / n,
        p_value=float(chi2_distribution.sf(chi, n)),
        residuals=residuals,
        notes=_step_notes(model, change),
    )
    return hyp, model


def _predict(
    system: System,
    faults: Sequence[_Fault],
    values: Mapping[str, float],
    resolved: ResolvedMeasurements,
    paths: Sequence[str],
    step: float | None,
) -> dict[tuple[int, str], float | None] | None:
    """Predictions of ``paths`` at every point under a fitted hypothesis (None when the
    model fails)."""
    with _with_leaks(system, faults):
        params = [
            _Param(f.path, f.unit, f.reference, f.lower, f.upper, float(values[f.path]))
            for f in faults
        ]
        wanted = {i: list(paths) for i in range(len(resolved.points))}
        try:
            model = _Model(system, params, resolved.points, wanted, step)
        except (InvalidValueError, MeasurementError):
            return None
        try:
            return model.predict(np.array([p.current for p in params]))
        except _ModelFailure:
            return None
        finally:
            model.reset()


def _fit_all(
    system: System,
    ms: Any,
    resolved: ResolvedMeasurements,
    step: float | None,
    singles: Sequence[_Fault],
    combos: Sequence[Sequence[_Fault]],
    skipped: dict[str, str],
) -> tuple[list[Hypothesis], dict[str, tuple[_Fault, ...]], dict[str, int], int, float | None]:
    """The baseline, then every single fault, then every combination (each starting from
    the better of its single-fault fits). A fit that fails is recorded in ``skipped``.

    Returns:
        The hypotheses (the baseline first), their faults and order by name, the number of
        model evaluations, and the baseline's simulation step of timed points (or None).
    """
    baseline, base_model = _baseline(system, resolved, step)
    evaluations = base_model.evaluations
    step_used = base_model.step if base_model.timed else None
    fitted: list[Hypothesis] = [baseline]
    by_ref: dict[str, Hypothesis] = {}
    order: dict[str, int] = {BASELINE: 0}
    faults_of: dict[str, tuple[_Fault, ...]] = {BASELINE: ()}
    for members in [*([f] for f in singles), *combos]:
        name = " + ".join(f.ref for f in members)
        start: dict[str, float] = {}
        if len(members) > 1:
            # Start from the better single fit (its fitted values, the other faults at the
            # baseline), so the combination fits at least as well as that single fault.
            fitted_singles = [by_ref[f.ref] for f in members if f.ref in by_ref]
            if fitted_singles:
                better = min(fitted_singles, key=lambda h: h.aic)
                start = {e.path: e.value for e in better.estimates.values()}
        try:
            hyp = _fit(system, ms, members, resolved, step, start)
        except CalibrationError as exc:
            skipped[name] = f"the fit failed: {exc}"
            continue
        evaluations += hyp.calibration.evaluations if hyp.calibration else 0
        order[hyp.name] = len(order)
        faults_of[hyp.name] = tuple(members)
        if len(members) == 1:
            by_ref[members[0].ref] = hyp
        fitted.append(hyp)
    return fitted, faults_of, order, evaluations, step_used


# ----------------------------------------------------------------------------------------
# diagnose
# ----------------------------------------------------------------------------------------
def diagnose(
    system: System,
    measurements: Any,
    hypotheses: Any = None,
    *,
    max_faults: int = 1,
    include_leaks: bool = False,
    step: Any = None,
) -> DiagnosisResult:
    """Rank fault hypotheses by how well they explain measurements (design 14.3).

    Scores the system as given (the baseline) and every hypothesis: each is fitted with
    :func:`~worldparts.calibrate` over its fault's range and scored by the Akaike
    information criterion on the weighted residuals, ``chi-square + 2 k``. See the module
    documentation for the method, including which hypotheses count as separate
    explanations (``supported``).

    Args:
        system: The system as believed healthy (with its commanded settings); left
            unchanged.
        measurements: A :class:`~worldparts.MeasurementSet`, its plain-dict form, or the
            path of a YAML, JSON or CSV file.
        hypotheses: Fault references to test: ``"<instance>.<fault>"`` for a fault mode
            declared in the component's manifest (``"pump.worn_impeller"``),
            ``"leak_at:<port>"`` for a leak at the junction of that port
            (``"leak_at:pump.outlet"``), or a list of those (or a string joined by ``+``)
            for faults together. Default: every fault mode of every component.
        max_faults: 1 (default) or 2: with 2, every pair of the single faults is tried too.
        include_leaks: Also test a leak at every junction (where two or more ports meet).
        step: Simulation step for timed points, as in :func:`~worldparts.calibrate`.

    Returns:
        A :class:`DiagnosisResult`.

    Raises:
        MeasurementError: The measurements are malformed, empty or do not fit the system.
        UnknownVariableError: A hypothesis names no fault mode or junction (the message
            lists the valid ones).
        InvalidValueError: A malformed or duplicate hypothesis, a hypothesis that cannot be
            fitted (its path is written by a control or set by the points, or its range is
            empty), or a bad ``max_faults``.
        SystemCheckError: The system has error-level check issues.
        CalibrationError: The system as given cannot be evaluated at the measured points.
    """
    started = _time.perf_counter()
    if isinstance(max_faults, bool) or not isinstance(max_faults, int):
        raise InvalidValueError(f"max_faults must be 1 or 2, got {max_faults!r}.")
    if not 1 <= max_faults <= MAX_FAULTS:
        raise InvalidValueError(
            f"max_faults must be 1 or {MAX_FAULTS}, got {max_faults}: combinations of more "
            "faults are rarely identifiable from plant measurements and multiply the fits."
        )
    ms = _measurement_set(measurements)
    if ms.n_values == 0:
        raise MeasurementError(
            "The measurement set has no measured values; add points with 'measured', e.g. "
            "{points: [{name: p1, measured: {pump.volume_flow: '20 m3/h'}}]}."
        )
    resolved = ms.resolve(system)
    step_s = _parse_step(step)
    _preflight(system)
    catalogue = _Faults(system, resolved.points)
    skipped: dict[str, str] = {}

    # --- the hypotheses to fit -----------------------------------------------------------
    singles: list[_Fault] = []
    combos: list[list[_Fault]] = []
    node_refs: dict[int, str] = {}  # junction -> the leak reference that names it

    def fault_for(ref: str, where: str, explicit: bool) -> _Fault | None:
        fault, reason = catalogue.resolve(ref, where)
        node = catalogue.node(fault)
        if node is not None:
            other = node_refs.setdefault(node, ref)
            if other != ref:
                if explicit:
                    raise InvalidValueError(
                        f"{where}: '{ref}' and '{other}' name the same junction; name it one way."
                    )
                return None  # a default leak at a junction already named
        if reason is not None:
            if explicit:
                raise InvalidValueError(f"{where}: '{ref}' cannot be tested: {reason}.")
            skipped[ref] = reason
            return None
        return fault

    def add_single(fault: _Fault | None) -> None:
        if fault is not None and all(f.ref != fault.ref for f in singles):
            singles.append(fault)

    if hypotheses is None:
        for ref in catalogue.default_modes():
            add_single(fault_for(ref, "default hypotheses", explicit=False))
    else:
        listed: set[frozenset[str]] = set()
        for refs, where in _hypothesis_entries(hypotheses):
            key = frozenset(refs)
            if len(key) != len(refs):
                raise InvalidValueError(f"{where}: {' + '.join(refs)} repeats a fault.")
            if key in listed:
                raise InvalidValueError(f"{where}: {' + '.join(refs)} is listed twice.")
            listed.add(key)
            members = [fault_for(ref, where, explicit=True) for ref in refs]
            faults = [f for f in members if f is not None]
            if len(refs) == 1:
                add_single(faults[0])
                continue
            paths = [f.path for f in faults]
            if len(set(paths)) != len(paths):
                raise InvalidValueError(
                    f"{where}: {' + '.join(refs)} varies {paths[0]} twice; a combination "
                    "needs faults in different paths."
                )
            combos.append(faults)
    if include_leaks:
        for ref in catalogue.default_leaks():
            add_single(fault_for(ref, "include_leaks", explicit=False))
    if max_faults >= 2:
        listed_combos = {frozenset(f.ref for f in m) for m in combos}
        for a, b in combinations(singles, 2):
            if a.path != b.path and frozenset((a.ref, b.ref)) not in listed_combos:
                combos.append([a, b])

    # --- fit -----------------------------------------------------------------------------
    fitted, faults_of, order, evaluations, baseline_step = _fit_all(
        system, ms, resolved, step_s, singles, combos, skipped
    )
    baseline = fitted[0]

    # --- rank ----------------------------------------------------------------------------
    # AIC to within _TIE: equal scores go to fewer faults, then to the order of the
    # hypotheses. The support rule below uses the same scores, so a hypothesis that only
    # adds faults to one that scores as well always ranks after it (the best is supported).
    score = {h.name: round(h.aic / _TIE) * _TIE for h in fitted}
    ranked = sorted(fitted, key=lambda h: (score[h.name], h.parameters, order[h.name]))
    best = ranked[0]
    for i, h in enumerate(ranked):
        h.rank = i + 1
        h.delta_aic = h.aic - best.aic
    sets = {h.name: frozenset(h.faults) for h in ranked}
    for h in ranked:
        extra_ok = {f.ref: f.contains_baseline for f in faults_of[h.name]}
        h.supported = not any(
            g is not h
            and sets[g.name] < sets[h.name]
            and all(extra_ok[r] for r in sets[h.name] - sets[g.name])
            and score[g.name] <= score[h.name]
            for g in ranked
        )
    supported = [h for h in ranked if h.supported]
    total = sum(math.exp(-h.delta_aic / 2) for h in supported)
    for h in supported:
        h.weight = math.exp(-h.delta_aic / 2) / total
    runner = next((h for h in ranked[1:] if h.supported), None)
    ambiguous = runner is not None and runner.delta_aic < AMBIGUITY_AIC
    plausible = [h.name for h in supported if h.delta_aic < AMBIGUITY_AIC]
    unexplained = best.p_value is not None and best.p_value < UNEXPLAINED_P
    false_alarm = None
    if best is not baseline:
        # P(max_h (drop of chi-square_h - 2 k_h) >= observed advantage | healthy) <=
        # sum_h P(chi2(k_h) >= advantage + 2 k_h): the drop of a hypothesis that contains
        # the baseline is at most chi-square with k_h degrees of freedom under no fault
        # (exactly so for an interior value, half of it on the edge of the range).
        advantage = baseline.aic - best.aic
        nested = [
            h
            for h in ranked
            if h is not baseline and all(f.contains_baseline for f in faults_of[h.name])
        ]
        false_alarm = min(
            1.0,
            sum(
                float(chi2_distribution.sf(advantage + 2 * h.parameters, h.parameters))
                for h in nested
            ),
        )

    discriminating = _discriminators(best, runner) if runner is not None else []
    suggestions: list[SensorSuggestion] = []
    if ambiguous and runner is not None:
        suggestions, n_eval = _suggest(
            system, resolved, best, runner, faults_of, step_s, set(ms.paths)
        )
        evaluations += n_eval
    notes = _notes(
        ranked,
        best,
        runner,
        ambiguous,
        plausible,
        unexplained,
        false_alarm,
        suggestions,
        skipped,
        max_faults,
        include_leaks,
    )
    if unexplained:
        conclusion = "unexplained"
    elif ambiguous:
        conclusion = "ambiguous"
    elif best is baseline:
        conclusion = "no_fault"
    elif false_alarm is not None and false_alarm > FALSE_ALARM_LEVEL:
        conclusion = "weak_evidence"
    else:
        conclusion = "fault"
    return DiagnosisResult(
        conclusion=conclusion,
        hypotheses=ranked,
        best=best.name,
        runner_up=None if runner is None else runner.name,
        ambiguous=ambiguous,
        plausible=plausible,
        unexplained=unexplained,
        false_alarm=false_alarm,
        discriminating=discriminating,
        suggested_sensors=suggestions,
        skipped=skipped,
        notes=notes,
        max_faults=max_faults,
        evaluations=evaluations,
        elapsed=_time.perf_counter() - started,
        step=baseline_step,
    )


# ----------------------------------------------------------------------------------------
# discrimination and notes
# ----------------------------------------------------------------------------------------
def _discriminators(best: Hypothesis, runner: Hypothesis) -> list[Discriminator]:
    """Measured values whose predictions under the two fits differ, most separated first."""
    theirs = {(r.point, r.path): r for r in runner.residuals}
    out: list[Discriminator] = []
    for r in best.residuals:
        o = theirs.get((r.point, r.path))
        if o is None or r.predicted is None or o.predicted is None:
            continue
        separation = (o.predicted - r.predicted) / r.sigma
        if abs(separation) < DISCRIMINATING_SEPARATION:
            continue
        mine, other = r.normalised or 0.0, o.normalised or 0.0
        out.append(
            Discriminator(
                r.point,
                r.path,
                r.unit,
                r.measured,
                r.sigma,
                r.predicted,
                o.predicted,
                separation,
                other**2 - mine**2,
                r.time,
                r.reference,
            )
        )
    out.sort(key=lambda d: (-abs(d.separation), d.point, d.path))
    return out[:MAX_LISTED]


def _suggest(
    system: System,
    resolved: ResolvedMeasurements,
    best: Hypothesis,
    runner: Hypothesis,
    faults_of: Mapping[str, Sequence[_Fault]],
    step: float | None,
    measured: set[str],
) -> tuple[list[SensorSuggestion], int]:
    """Unmeasured variables that the fits of ``best`` and ``runner`` predict differently,
    by at least :data:`SUGGESTION_SEPARATION` default uncertainties at some point.

    Returns:
        The suggestions, most separated first, and the number of model evaluations.
    """
    candidates = [p for p in _default_candidates(system) if p not in measured]
    if not candidates:
        return [], 0
    info = measurable_paths(system)
    predictions = []
    for h in (best, runner):
        values = {e.path: e.value for e in h.estimates.values()}
        pred = _predict(system, faults_of[h.name], values, resolved, candidates, step)
        if pred is None:
            return [], len(predictions) + 1
        predictions.append(pred)
    out: list[SensorSuggestion] = []
    for path in candidates:
        v = info[path]
        unit = v.unit or "1"
        top: tuple[float, str, float, float, float] | None = None
        try:
            for i, point in enumerate(resolved.points):
                a, b = predictions[0].get((i, path)), predictions[1].get((i, path))
                if a is None or b is None or not (math.isfinite(a) and math.isfinite(b)):
                    continue
                sigma = default_sigma(a, unit, v.pressure_reference)
                sep = abs(b - a) / sigma
                if top is None or sep > top[0]:
                    top = (sep, point.name, a, b, sigma)
        except InvalidValueError:  # a kind of quantity with no default uncertainty
            continue
        if top is not None and top[0] >= SUGGESTION_SEPARATION:
            sep, point_name, a, b, sigma = top
            out.append(
                SensorSuggestion(path, unit, point_name, a, b, sigma, sep, v.pressure_reference)
            )
    out.sort(key=lambda s: (-s.separation, s.path))
    return out[:MAX_LISTED], 2


def _aic_text(d: float) -> str:
    """An AIC difference for a note: two decimals when small, three digits otherwise."""
    return f"{d:.2f}" if abs(d) < 10 else f"{d:.3g}"


def _estimate_text(e: FaultEstimate) -> str:
    unit = _unit_suffix(e.unit)
    if e.verdict == "not_identifiable" or e.standard_error is None:
        text = f"{e.path} not determined by the measurements (left at {e.value:.4g}{unit})"
    else:
        text = f"{e.path} = {e.value:.4g} +- {e.standard_error:.2g}{unit}"
    if e.factor is not None:
        text += f", {e.factor:.3g} times the value {e.baseline:.4g}{unit} of the system as given"
    text += f"; healthy {e.healthy:g}{unit}"
    if e.leak_flow:
        flows = [f"{v:.3g} L/min at '{k}'" for k, v in e.leak_flow.items() if v is not None]
        if flows:
            text += "; leaking " + ", ".join(flows[:3]) + (" ..." if len(flows) > 3 else "")
    return text


def _hypothesis_text(h: Hypothesis) -> str:
    if not h.estimates:
        return h.name
    return f"{h.name} (" + "; ".join(_estimate_text(e) for e in h.estimates.values()) + ")"


def _notes(
    ranked: Sequence[Hypothesis],
    best: Hypothesis,
    runner: Hypothesis | None,
    ambiguous: bool,
    plausible: Sequence[str],
    unexplained: bool,
    false_alarm: float | None,
    suggestions: Sequence[SensorSuggestion],
    skipped: Mapping[str, str],
    max_faults: int,
    include_leaks: bool,
) -> list[str]:
    """Plain-language conclusions, most important first."""
    notes: list[str] = []
    base = next(h for h in ranked if h.name == BASELINE)
    fit = f"chi-square {best.chi_square:.3g} for {best.measurements} measured values" + (
        f", p = {best.p_value:.2g}" if best.p_value is not None else ""
    )
    if best is base:
        text = f"No fault is detected: the system as given explains the measurements best ({fit})."
        if runner is None:
            text += (
                " No fault hypothesis improves the fit by more than the 2 AIC units that its "
                "fitted parameter costs."
            )
        closest = next((h for h in ranked if len(h.faults) == 1), None)
        if closest is not None:
            text += f" The closest single fault is {_hypothesis_text(closest)}."
        notes.append(text)
    else:
        text = f"Best explanation: {_hypothesis_text(best)} ({fit})."
        text += f" It is {_aic_text(base.aic - best.aic)} AIC units better than no fault"
        if runner is not None and runner is not base:
            text += f" and {_aic_text(runner.delta_aic)} better than the runner-up, {runner.name}"
        notes.append(text + ".")
        if false_alarm is not None:
            tested = len(ranked) - 1
            chance = (
                f"A healthy plant would give one of the {tested} hypotheses tested this much "
                f"advantage over no fault by chance with probability at most "
                f"{max(false_alarm, 1e-15):.2g}"
            )
            if false_alarm > FALSE_ALARM_LEVEL:
                notes.append(
                    f"The evidence for a fault is weak. {chance}; confirm with more "
                    "measurements before acting."
                )
            else:
                notes.append(chance + ".")
    if ambiguous and runner is not None:
        shown = [h for h in ranked if h.name in plausible]
        weights = ", ".join(f"{h.name} {h.weight:.2f}" for h in shown[:6] if h.weight is not None)
        more = ", ..." if len(shown) > 6 else ""
        notes.append(
            f"Ambiguous: {len(shown)} hypotheses explain the measurements within "
            f"{AMBIGUITY_AIC:g} AIC units of the best, so the measurements cannot tell them "
            f"apart (Akaike weights: {weights}{more})."
        )
        if suggestions:
            s = suggestions[0]
            also = [x.path for x in suggestions[1:4]]
            notes.append(
                f"To resolve it, measure {s.path}: the fits of {best.name} and {runner.name} "
                f"predict {s.best:.4g} and {s.runner_up:.4g}{_unit_suffix(s.unit)} at point "
                f"'{s.point}', {s.separation:.2g} default uncertainties apart"
                + (f" (also: {', '.join(also)})" if also else "")
                + ". A refit that includes the new measurement can narrow the gap."
            )
        else:
            notes.append(
                "No unmeasured variable separates the best and the runner-up by one default "
                "uncertainty at these operating points; measure at other operating points "
                "(other settings) instead."
            )
    if unexplained:
        tries = []
        if not include_leaks:
            tries.append("include_leaks=True")
        if max_faults < MAX_FAULTS:
            tries.append(f"max_faults={MAX_FAULTS}")
        notes.append(
            "Even the best hypothesis misses the measurements by more than the stated "
            f"uncertainties explain (reduced chi-square {best.reduced_chi_square:.3g}, "
            f"p = {best.p_value:.2g}): the fault may not be among the hypotheses"
            + (f" (try {' or '.join(tries)})" if tries else "")
            + ", or the uncertainties are too small, which also overstates every AIC "
            "difference. Read the largest residuals of the best hypothesis."
        )
    for e in best.estimates.values():
        unit = _unit_suffix(e.unit)
        if e.at_bound in ("lower", "upper"):
            notes.append(
                f"{e.fault}: the fit sits at the {e.at_bound} end of its range ({e.value:g}"
                f"{unit}) and the data push further, so the fault may be larger than its "
                "range allows, or another fault is at work."
            )
        elif e.at_bound == "model_limit":
            notes.append(
                f"{e.fault}: the fit ends next to values where the model cannot be evaluated; "
                "treat the estimate with caution."
            )
        if e.verdict == "weak":
            notes.append(
                f"{e.fault}: weakly determined; read {e.path} with its standard error (and "
                "the correlations in the hypothesis's calibration)."
            )
    if not best.success:
        notes.append(
            f"The fit of {best.name} ended worse than its start, so its starting values are "
            "reported; see its notes."
        )
    defaults = sum(1 for r in base.residuals if r.sigma_default)
    if defaults:
        notes.append(
            f"{defaults} of {base.measurements} measured values use the default uncertainty "
            "(1 % of the value, floored per kind of quantity). AIC differences scale with "
            "1 / sigma**2, so give 'sigma' where the instrument accuracy is known."
        )
    if skipped:
        shown_skips = list(skipped.items())[:8]
        notes.append(
            "Not tested: "
            + "; ".join(f"{k}: {v}" for k, v in shown_skips)
            + (f"; and {len(skipped) - 8} more (see skipped)" if len(skipped) > 8 else "")
            + "."
        )
    return notes
