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
  the system's values, so a fit is never worse than the baseline it contains. A fault is
  fitted only on its side of the system as given: a system that already has some wear is
  never "diagnosed" with the wear moving back toward new. A leak hypothesis adds a ``leak``
  component at the junction for the fit (its orifice diameter varied from the smallest leak
  the component represents up to the bore of the pipes that meet there) and removes it
  afterwards. A combination starts from the best fit of the hypotheses it contains; the
  smaller hypotheses of a combination given explicitly are fitted too (not ranked), so
  that every fault of a combination can be checked against the others.
- **Score.** The Akaike information criterion on the weighted residuals, ``AIC =
  chi-square + 2 k`` with ``k`` the number of fitted fault parameters (``0`` for the
  baseline). Lower is better; hypotheses are ranked by it.
- **Support.** A hypothesis that adds faults to one that scores at least as well (for
  example a pair that adds a second fault to the best single fault, or any fault that does
  not beat the baseline it contains) adds a parameter that the data do not support (Arnold
  2010, "Uninformative parameters and model selection using Akaike's Information
  Criterion"): it is ranked but is not a separate explanation, so it is never the
  runner-up and gets no Akaike weight.
- **Evidence.** ``false_alarm`` bounds the chance that noise alone gives the best
  hypothesis its advantage over each hypothesis it contains (no fault, and for a
  combination each smaller combination), with a Bonferroni sum over every tested hypothesis
  that extends that one; above :data:`FALSE_ALARM_LEVEL` the evidence is weak, and the
  notes name the fault it is weak for.
- **Ambiguity.** The runner-up is the best-ranked supported hypothesis after the best; the
  diagnosis is ambiguous when it scores within :data:`AMBIGUITY_AIC` (2) of the best. The
  result lists the measurements that discriminate the best from the runner-up and, when
  ambiguous, the unmeasured variables that separate the plausible hypotheses.
- **Blind spots.** A fault that no measured value responds to is ``undetectable``: "no
  fault" does not rule it out, and the result names a sensor that would see it.

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
from itertools import combinations, pairwise
from typing import Any

import numpy as np
from scipy.stats import chi2 as chi2_distribution

from worldparts.calibration import (
    _MODEL_FAILURES,
    CalibrationResult,
    Residual,
    _calibrate,
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
#: A fault is concluded only when the chance that noise alone gives the best hypothesis its
#: advantage over each hypothesis it contains (``false_alarm``) is at most this; above it
#: the evidence is weak.
FALSE_ALARM_LEVEL: float = 0.05
#: Values of :attr:`DiagnosisResult.conclusion`, in order of precedence.
CONCLUSIONS: tuple[str, ...] = (
    "unexplained",
    "untested",
    "ambiguous",
    "no_fault",
    "weak_evidence",
    "fault",
)
#: At most this many discriminating measurements and sensor suggestions are listed.
MAX_LISTED: int = 10
#: Sensor suggestions separate plausible hypotheses by at least this many default
#: uncertainties at some operating point.
SUGGESTION_SEPARATION: float = 1.0
#: Measurements whose predictions under the best and the runner-up differ by less than this
#: many standard uncertainties are not listed as discriminating.
DISCRIMINATING_SEPARATION: float = 0.1

#: Catalogue alias of the component a leak hypothesis adds.
_LEAK_TYPE = "leak"
#: AIC values closer than this are ties (broken by fewer faults, then by order).
_TIE = 1e-6
#: A range spanning at least this ratio (a leak's diameter) is halved geometrically when
#: looking for a sensor that sees a fault.
_GEOMETRIC_RATIO = 100.0


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
        lower: Lower end of the range fitted: the fault's range on its side of the system
            as given (see :attr:`baseline`).
        upper: Upper end of the range fitted, likewise.
        healthy: The value without the fault (for a leak, the smallest leak the component
            represents, which is negligible).
        baseline: The value in the system as given (None for a leak: there is none). When
            it differs from ``healthy``, the fault is fitted only from there away from
            healthy.
        verdict: ``identifiable``, ``weak`` or ``not_identifiable`` (as in calibration).
        at_bound: ``lower``, ``upper`` or ``model_limit`` when the data push the fit onto
            the edge of the range (see :class:`~worldparts.ParameterEstimate`), else None.
            Which end it is matters: see :attr:`bound_end`.
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

    @property
    def start(self) -> float:
        """The value the fit starts from: the value in the system as given, or a leak's
        healthy (smallest) diameter."""
        return self.healthy if self.baseline is None else self.baseline

    @property
    def bound_end(self) -> str | None:
        """For a fit pushed onto a bound (``at_bound`` ``lower`` or ``upper``): ``start``
        when that bound is the value the fit starts from (the data push away from the
        fault: past the healthy value, or back toward healthy from a faulty system as
        given), ``far`` when it is the far end of the range (the fault may be larger than
        the range allows); None otherwise."""
        if self.at_bound not in ("lower", "upper"):
            return None
        pinned = self.lower if self.at_bound == "lower" else self.upper
        tol = 1e-9 * max(1.0, abs(self.lower), abs(self.upper))
        return "start" if abs(pinned - self.start) <= tol else "far"

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
            if self.bound_end is not None:
                out["bound_end"] = self.bound_end
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

    @property
    def detectable(self) -> bool:
        """False when every fault of the hypothesis is ``not_identifiable``: no measured
        value responds to it, so the data can neither support nor rule it out."""
        return not self.estimates or any(
            e.verdict != "not_identifiable" for e in self.estimates.values()
        )

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
    """An unmeasured variable whose predictions differ between the plausible hypotheses of
    an ambiguous diagnosis: measuring it separates some of them.

    At the operating point where it separates the most pairs, the plausible hypotheses are
    sorted by their predictions and split wherever two neighbours are at least
    :data:`SUGGESTION_SEPARATION` default uncertainties apart: hypotheses in different
    ``groups`` are separated, those within a group are not (conservatively: a group may
    chain several small steps). The predictions are those of the current fits; a refit that
    includes the new measurement can narrow the gaps, so ``separation`` is what the sensor
    could show, not a promise.

    Attributes:
        path: The variable.
        unit: Its display unit (of the predictions and ``sigma``).
        point: The operating point where it separates the most pairs.
        best: Predicted under the best hypothesis there.
        runner_up: Predicted under the runner-up there.
        sigma: The default uncertainty of such a measurement there (1 % of the largest
            prediction, floored per kind of quantity).
        separation: The smallest gap between two groups, in default uncertainties.
        reference: Pressure reference, if any.
        predictions: Every plausible hypothesis's prediction there.
        groups: The plausible hypotheses in groups that this sensor does not tell apart,
            ordered by prediction.
        separated: Pairs of plausible hypotheses that it separates.
        pairs: Pairs of plausible hypotheses in all.
    """

    path: str
    unit: str
    point: str
    best: float
    runner_up: float
    sigma: float
    separation: float
    reference: str | None = None
    predictions: dict[str, float] = field(default_factory=dict)
    groups: tuple[tuple[str, ...], ...] = ()
    separated: int = 1
    pairs: int = 1

    @property
    def resolves(self) -> bool:
        """True when it separates every pair of plausible hypotheses."""
        return self.separated == self.pairs

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
            "predictions": {k: _num(v) for k, v in self.predictions.items()},
            "groups": [list(g) for g in self.groups],
            "separated": self.separated,
            "pairs": self.pairs,
            "resolves": self.resolves,
        }
        if self.reference:
            out["reference"] = self.reference
        return out


@dataclass
class DiagnosisResult:
    """The result of :func:`diagnose`.

    Attributes:
        conclusion: The verdict in one word, the first that applies: ``unexplained`` (even
            the best hypothesis misses the data), ``untested`` (no fault hypothesis could
            be tested: each was skipped or is undetectable, and the system as given fits),
            ``ambiguous`` (the runner-up is within :data:`AMBIGUITY_AIC`), ``no_fault``
            (the baseline is best among the testable hypotheses; see ``undetectable``),
            ``weak_evidence`` (a fault is best, but ``false_alarm`` exceeds
            :data:`FALSE_ALARM_LEVEL`) or ``fault``.
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
            that noise alone (with the model and the uncertainties right) gives the best
            hypothesis its AIC advantage over a hypothesis it contains, taken for the one
            it contains that gives the largest bound (no fault, and for a combination each
            smaller combination fitted). For a contained hypothesis ``g`` with the
            advantage ``a = AIC_g - AIC_best``, the bound is the Bonferroni sum over the
            tested hypotheses ``h`` that extend ``g`` of ``P(chi2(k_h - k_g) >= a + 2 (k_h -
            k_g))`` (the chi-square drop of a nested fit under ``g`` is at most chi-square
            with ``k_h - k_g`` degrees of freedom); undetectable extensions cannot drop the
            chi-square and are left out. Near 1 means that the evidence is weak. None when
            the best hypothesis is the baseline, or when the result is unexplained (the
            bound assumes that the uncertainties explain the misfit).
        false_alarm_against: The contained hypothesis that gives ``false_alarm``:
            ``baseline`` (the evidence for any fault) or a smaller combination (the
            evidence for the extra faults); None with ``false_alarm``.
        discriminating: Measured values whose predictions differ between the best and the
            runner-up, most separated first.
        suggested_sensors: When ambiguous, unmeasured variables that separate plausible
            hypotheses, those that separate the most pairs first.
        undetectable: Fault hypotheses that no measured value responds to (every fault of
            the fit ``not_identifiable``), each with an unmeasured variable that a fault of
            half its range would move by at least one default uncertainty (None when none
            would): the conclusion says nothing about them.
        skipped: Hypotheses that were not evaluated, with the reason (for example a fault
            whose input a control writes, or a model that fails at the starting values).
        notes: Plain-language conclusions and caveats for an agent, most important first.
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
    false_alarm_against: str | None = None
    undetectable: dict[str, str | None] = field(default_factory=dict)

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
            "false_alarm_against": self.false_alarm_against,
            "notes": list(self.notes),
            "hypotheses": [h.to_dict(residuals) for h in shown],
            "hypotheses_total": len(self.hypotheses),
            "discriminating": [d.to_dict() for d in self.discriminating],
            "suggested_sensors": [s.to_dict() for s in self.suggested_sensors],
            "undetectable": dict(self.undetectable),
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
    def start(self) -> float:
        """The value a fit starts from: the system's, or a leak's smallest diameter."""
        return self.healthy if self.baseline is None else self.baseline

    @property
    def contains_baseline(self) -> bool:
        """Whether the fault's range contains the system as given (a leak's smallest
        diameter is a negligible leak), so the hypothesis includes the baseline."""
        if self.baseline is None:
            return True
        tol = 1e-12 * max(1.0, abs(self.lower), abs(self.upper))
        return self.lower - tol <= self.baseline <= self.upper + tol

    def typical(self) -> float:
        """A fault of about half its range: halfway from :attr:`start` to the far end of
        the range (geometrically for a range of two decades or more, such as a leak's
        diameter)."""
        start = min(max(self.start, self.lower), self.upper)
        far = self.upper if self.upper - start >= start - self.lower else self.lower
        lo, hi = min(start, far), max(start, far)
        if lo > 0 and hi / lo >= _GEOMETRIC_RATIO:
            return math.sqrt(lo * hi)
        return 0.5 * (start + far)

    def bounds(self) -> dict[str, Any]:
        return {"path": self.path, "lower": self.lower, "upper": self.upper}


def _fault_side(lower: float, upper: float, healthy: float, current: float) -> tuple[float, float]:
    """The part of ``[lower, upper]`` on the fault's side of ``current``.

    A fault grows away from ``healthy``: upwards when ``healthy`` is the lower end, downwards
    when it is the upper end, and, for a range around ``healthy``, the way ``current``
    already deviates from it. Values between ``healthy`` and ``current`` would mean a plant
    better than the system as given (a repair or a recalibration), not this fault.
    """
    tol = 1e-12 * max(1.0, abs(lower), abs(upper))
    if abs(current - healthy) <= tol:
        return lower, upper
    if healthy <= lower + tol:
        up = True
    elif healthy >= upper - tol:
        up = False
    else:
        up = current > healthy
    return (max(lower, current), upper) if up else (lower, min(upper, current))


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
        declared = (lower, upper)
        if not spec.relative:  # a relative range is declared around the current value
            lower, upper = _fault_side(lower, upper, healthy, current)
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
            if (lower, upper) != declared and declared[0] < declared[1]:
                return fault, (
                    f"the system as given already has {path} = {current:g}{unit}, at or beyond "
                    f"the end of the fault's range [{declared[0]:g}, {declared[1]:g}]{unit} "
                    f"away from healthy ({healthy:g}{unit}), so this fault cannot grow from there"
                )
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
    start_step: float | None,
) -> Hypothesis:
    """Calibrate the faults of one hypothesis; the system is left as it was.

    Raises:
        CalibrationError: The model cannot be evaluated at the starting values.
    """
    with _with_leaks(system, faults), _with_values(system, start):
        cal = _calibrate(
            system,
            ms,
            [f.bounds() for f in faults],
            step=step,
            checked=True,
            start_step=start_step,
        )
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
    auto_step: float | None,
) -> dict[tuple[int, str], float | None] | None:
    """Predictions of ``paths`` at every point with the faults at ``values`` (None when the
    model fails). Timed points use ``step`` as given, or else the automatic step
    ``auto_step`` that the fit found accurate, with its extrapolation."""
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
        if model.extrapolate and auto_step is not None:
            model.step = min(model.step, auto_step)
        try:
            return model.predict(np.array([p.current for p in params]))
        except _ModelFailure:
            return None
        finally:
            model.reset()


def _auto_step(h: Hypothesis, fallback: float | None) -> float | None:
    """The automatic step of the timed points that the fit of ``h`` ended with."""
    cal = h.calibration
    if cal is not None and cal.step is not None and cal.step_extrapolated:
        return cal.step
    return fallback


# ----------------------------------------------------------------------------------------
# diagnose
# ----------------------------------------------------------------------------------------
@dataclass
class _Fits:
    """Every fit of a diagnosis: the ranked (listed) hypotheses and the smaller hypotheses
    of explicit combinations (fitted to check their faults, not ranked)."""

    baseline: Hypothesis
    listed: list[Hypothesis]
    by_set: dict[frozenset[str], Hypothesis]
    faults_of: dict[str, tuple[_Fault, ...]]
    order: dict[str, int]
    references: set[str]
    evaluations: int
    base_step: float | None
    failed: set[frozenset[str]] = field(default_factory=set)

    def fit(
        self,
        system: System,
        ms: Any,
        members: Sequence[_Fault],
        resolved: ResolvedMeasurements,
        step: float | None,
        skipped: dict[str, str],
        *,
        listed: bool,
        purpose: str = "",
    ) -> Hypothesis | None:
        """Fit one hypothesis, starting from the best fit of those it contains (the
        baseline for a single fault), so it never fits worse than they do."""
        name = " + ".join(f.ref for f in members)
        key = frozenset(f.ref for f in members)
        contained = [h for k, h in self.by_set.items() if k < key]
        origin = min(contained, key=lambda h: h.chi_square)
        start = {e.path: e.value for e in origin.estimates.values()}
        try:
            auto = _auto_step(origin, self.base_step)
            hyp = _fit(system, ms, members, resolved, step, start, auto)
        except CalibrationError as exc:
            skipped[name] = f"the fit failed{purpose}: {exc}"
            self.failed.add(key)
            return None
        self.evaluations += hyp.calibration.evaluations if hyp.calibration else 0
        self.by_set[key] = hyp
        self.faults_of[hyp.name] = tuple(members)
        if listed:
            self.order[hyp.name] = len(self.order)
            self.listed.append(hyp)
        else:
            self.references.add(hyp.name)
        return hyp


@dataclass
class _Evidence:
    """The false-alarm bound of the best hypothesis against one hypothesis it contains."""

    against: Hypothesis
    bound: float
    family: int
    reference: bool


def _evidence(best: Hypothesis, fits: _Fits, undetectable: set[str]) -> list[_Evidence]:
    """The false-alarm bound of ``best`` against every fitted hypothesis ``g`` it contains
    (the baseline, and for a combination each smaller one whose extra faults contain the
    values ``g`` uses), largest first.

    The bound is ``sum_h P(chi2(k_h - k_g) >= a + 2 (k_h - k_g))`` with ``a = AIC_g -
    AIC_best``, over ``best`` and every other tested (ranked) hypothesis ``h`` that extends
    ``g`` with faults whose ranges contain the values ``g`` uses (nested: under ``g`` the drop
    of ``h``'s chi-square is at most chi-square with ``k_h - k_g`` degrees of freedom,
    exactly so for an interior value, half of it on the edge of the range). Extensions by an
    undetectable fault are left out: their chi-square cannot drop.
    """
    mine = set(best.faults)
    out: list[_Evidence] = []
    for key, g in fits.by_set.items():
        if not key < mine:
            continue
        extra_best = [f for f in fits.faults_of[best.name] if f.ref not in key]
        if key and not all(f.contains_baseline for f in extra_best):
            continue
        gain = g.aic - best.aic
        terms: list[float] = []
        for h in fits.listed:
            if h is not best:
                extra = set(h.faults) - key
                if not key < set(h.faults) or extra & undetectable:
                    continue
                if not all(f.contains_baseline for f in fits.faults_of[h.name] if f.ref in extra):
                    continue
            dk = h.parameters - g.parameters
            terms.append(float(chi2_distribution.sf(gain + 2 * dk, dk)))
        out.append(_Evidence(g, min(1.0, sum(terms)), len(terms), g.name in fits.references))
    out.sort(key=lambda e: (-e.bound, len(e.against.faults)))
    return out


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
    :func:`~worldparts.calibrate` over its fault's range (on the fault's side of the system
    as given) and scored by the Akaike information criterion on the weighted residuals,
    ``chi-square + 2 k``. See the module documentation for the method, including which
    hypotheses count as separate explanations (``supported``) and how the evidence for a
    fault is bounded (``false_alarm``).

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
        max_faults: 1 (default) or 2: with 2, every pair of the detectable single faults is
            tried too.
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
                # A default leak at a junction already named: test it under that name.
                return catalogue.resolve(other, where)[0]
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
        listed_sets: set[frozenset[str]] = set()
        for refs, where in _hypothesis_entries(hypotheses):
            key = frozenset(refs)
            if len(key) != len(refs):
                raise InvalidValueError(f"{where}: {' + '.join(refs)} repeats a fault.")
            if key in listed_sets:
                raise InvalidValueError(f"{where}: {' + '.join(refs)} is listed twice.")
            listed_sets.add(key)
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

    # --- fit: the baseline, the single faults, then the combinations ---------------------
    baseline, base_model = _baseline(system, resolved, step_s)
    fits = _Fits(
        baseline=baseline,
        listed=[baseline],
        by_set={frozenset(): baseline},
        faults_of={BASELINE: ()},
        order={BASELINE: 0},
        references=set(),
        evaluations=base_model.evaluations,
        base_step=base_model.step if base_model.timed and base_model.extrapolate else None,
    )
    for f in singles:
        fits.fit(system, ms, [f], resolved, step_s, skipped, listed=True)
    blind = {h.faults[0] for h in fits.listed[1:] if not h.detectable}
    if max_faults >= 2:
        # A fault that no measured value responds to adds nothing to a pair, so pairs with
        # it are not fitted (it is reported as undetectable).
        listed_combos = {frozenset(f.ref for f in m) for m in combos}
        for a, b in combinations(singles, 2):
            if a.ref in blind or b.ref in blind or a.path == b.path:
                continue
            if frozenset((a.ref, b.ref)) not in listed_combos:
                combos.append([a, b])
    # The smaller hypotheses of each combination that are not listed are fitted too (not
    # ranked), so that every fault of a combination is checked against the others.
    planned = {frozenset(f.ref for f in m) for m in combos}
    jobs: list[tuple[list[_Fault], bool, str]] = [(m, True, "") for m in combos]
    seen: set[frozenset[str]] = set()
    for m in combos:
        for drop in m:
            sub = [f for f in m if f is not drop]
            key = frozenset(f.ref for f in sub)
            if key in fits.by_set or key in planned or key in seen or key in fits.failed:
                continue
            seen.add(key)
            purpose = f" (fitted to check {' + '.join(f.ref for f in m)})"
            jobs.append((sub, False, purpose))
    jobs.sort(key=lambda job: len(job[0]))  # stable: smaller hypotheses first
    for members, is_listed, purpose in jobs:
        fits.fit(system, ms, members, resolved, step_s, skipped, listed=is_listed, purpose=purpose)
    blind |= {h.faults[0] for h in fits.by_set.values() if len(h.faults) == 1 and not h.detectable}

    # --- rank ----------------------------------------------------------------------------
    # AIC to within _TIE: equal scores go to fewer faults, then to the order of the
    # hypotheses. The support rule below uses the same scores, so a hypothesis that only
    # adds faults to one that scores as well always ranks after it (the best is supported).
    fitted = fits.listed
    faults_of = fits.faults_of
    score = {h.name: round(h.aic / _TIE) * _TIE for h in fitted}
    ranked = sorted(fitted, key=lambda h: (score[h.name], h.parameters, fits.order[h.name]))
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
    testable = [h for h in ranked if h is not baseline and h.detectable]
    evidence: list[_Evidence] = []
    if best is not baseline and not unexplained:
        evidence = _evidence(best, fits, blind)
    false_alarm = evidence[0].bound if evidence else None
    against = evidence[0].against.name if evidence else None

    # --- discrimination and blind spots --------------------------------------------------
    evaluations = fits.evaluations
    measured_paths = set(ms.paths)
    discriminating = _discriminators(best, runner) if runner is not None else []
    suggestions: list[SensorSuggestion] = []
    if ambiguous and runner is not None:
        shown = [h for h in ranked if h.name in plausible]
        suggestions, n_eval = _suggest(
            system,
            resolved,
            shown,
            best,
            runner,
            faults_of,
            step_s,
            fits.base_step,
            measured_paths,
        )
        evaluations += n_eval
    undetectable: dict[str, str | None] = {}
    sightings: dict[str, tuple[str, str, float] | None] = {}
    blind_faults = [
        f for f in dict.fromkeys(f for fs in faults_of.values() for f in fs) if f.ref in blind
    ]
    if blind_faults:
        sightings, n_eval = _sightings(
            system, resolved, blind_faults, step_s, fits.base_step, measured_paths
        )
        evaluations += n_eval
        undetectable = {ref: (s[0] if s else None) for ref, s in sightings.items()}

    if unexplained:
        conclusion = "unexplained"
    elif best is baseline and not testable:
        conclusion = "untested"
    elif ambiguous:
        conclusion = "ambiguous"
    elif best is baseline:
        conclusion = "no_fault"
    elif false_alarm is not None and false_alarm > FALSE_ALARM_LEVEL:
        conclusion = "weak_evidence"
    else:
        conclusion = "fault"
    notes = _notes(
        _Summary(
            ranked=ranked,
            best=best,
            baseline=baseline,
            runner=runner,
            ambiguous=ambiguous,
            plausible=plausible,
            unexplained=unexplained,
            conclusion=conclusion,
            evidence=evidence,
            suggestions=suggestions,
            sightings=sightings,
            testable=testable,
            singles=[h for h in ranked if len(h.faults) == 1],
            skipped=skipped,
            max_faults=max_faults,
            include_leaks=include_leaks,
            fits=fits,
        )
    )
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
        step=base_model.step if base_model.timed else None,
        false_alarm_against=against,
        undetectable=undetectable,
    )


# ----------------------------------------------------------------------------------------
# discrimination
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


def _sigma_of(values: Sequence[float], unit: str, reference: str | None) -> float:
    """The default uncertainty of a sensor reading any of ``values``: the largest.

    Raises:
        InvalidValueError: The unit's kind of quantity has no default uncertainty.
    """
    return max(default_sigma(v, unit, reference) for v in values)


def _groups(
    values: Mapping[str, float], sigma: float
) -> tuple[tuple[tuple[str, ...], ...], int, float]:
    """The hypotheses sorted by ``values`` and split wherever neighbours are at least
    :data:`SUGGESTION_SEPARATION` ``sigma`` apart; with the number of pairs in different
    groups and the smallest gap between groups (in ``sigma``, infinite with one group)."""
    order = sorted(values, key=lambda n: (values[n], n))
    groups: list[list[str]] = [[order[0]]]
    gap = math.inf
    for prev, name in pairwise(order):
        step = (values[name] - values[prev]) / sigma
        if step >= SUGGESTION_SEPARATION:
            groups.append([name])
            gap = min(gap, step)
        else:
            groups[-1].append(name)
    n = len(order)
    separated = n * (n - 1) // 2 - sum(len(g) * (len(g) - 1) // 2 for g in groups)
    return tuple(tuple(g) for g in groups), separated, gap


def _suggest(
    system: System,
    resolved: ResolvedMeasurements,
    plausible: Sequence[Hypothesis],
    best: Hypothesis,
    runner: Hypothesis,
    faults_of: Mapping[str, Sequence[_Fault]],
    step: float | None,
    base_step: float | None,
    measured: set[str],
) -> tuple[list[SensorSuggestion], int]:
    """Unmeasured variables that separate plausible hypotheses by at least
    :data:`SUGGESTION_SEPARATION` default uncertainties at some point.

    Each candidate is judged at the point where it separates the most pairs of plausible
    hypotheses (then the largest smallest gap); candidates are ranked the same way, so the
    ranking does not depend on the order in which the hypotheses were listed.

    Returns:
        The suggestions, best first, and the number of model evaluations.
    """
    candidates = [p for p in _default_candidates(system) if p not in measured]
    if not candidates:
        return [], 0
    info = measurable_paths(system)
    predictions: dict[str, dict[tuple[int, str], float | None]] = {}
    for h in plausible:
        values = {e.path: e.value for e in h.estimates.values()}
        auto = _auto_step(h, base_step)
        pred = _predict(system, faults_of[h.name], values, resolved, candidates, step, auto)
        if pred is not None:
            predictions[h.name] = pred
    evaluations = len(plausible)
    if best.name not in predictions or runner.name not in predictions or len(predictions) < 2:
        return [], evaluations
    names = sorted(predictions)
    pairs = len(names) * (len(names) - 1) // 2
    out: list[tuple[tuple[int, float], SensorSuggestion]] = []
    for path in candidates:
        v = info[path]
        unit = v.unit or "1"
        top: tuple[tuple[int, float], SensorSuggestion] | None = None
        for i, point in enumerate(resolved.points):
            at = {n: predictions[n].get((i, path)) for n in names}
            if any(x is None or not math.isfinite(x) for x in at.values()):
                continue
            values = {n: float(x) for n, x in at.items() if x is not None}
            try:
                sigma = _sigma_of(list(values.values()), unit, v.pressure_reference)
            except InvalidValueError:  # a kind of quantity with no default uncertainty
                break
            groups, separated, gap = _groups(values, sigma)
            if separated == 0:
                continue
            key = (separated, gap)
            if top is None or key > top[0]:
                top = (
                    key,
                    SensorSuggestion(
                        path,
                        unit,
                        point.name,
                        values[best.name],
                        values[runner.name],
                        sigma,
                        gap,
                        v.pressure_reference,
                        values,
                        groups,
                        separated,
                        pairs,
                    ),
                )
        if top is not None:
            out.append(top)
    out.sort(key=lambda t: (-t[0][0], -t[0][1], t[1].path))
    return [s for _, s in out[:MAX_LISTED]], evaluations


def _sightings(
    system: System,
    resolved: ResolvedMeasurements,
    faults: Sequence[_Fault],
    step: float | None,
    base_step: float | None,
    measured: set[str],
) -> tuple[dict[str, tuple[str, str, float] | None], int]:
    """For each undetectable fault, the unmeasured variable that a fault of about half its
    range (:meth:`_Fault.typical`) moves most, in default uncertainties: ``(path, point,
    separation)``, or None when none moves by :data:`SUGGESTION_SEPARATION`.

    Returns:
        The sightings by fault reference and the number of model evaluations.
    """
    out: dict[str, tuple[str, str, float] | None] = {f.ref: None for f in faults}
    candidates = [p for p in _default_candidates(system) if p not in measured]
    if not candidates:
        return out, 0
    info = measurable_paths(system)
    base = _predict(system, [], {}, resolved, candidates, step, base_step)
    evaluations = 1
    if base is None:
        return out, evaluations
    for f in faults:
        pred = _predict(system, [f], {f.path: f.typical()}, resolved, candidates, step, base_step)
        evaluations += 1
        if pred is None:
            continue
        top: tuple[float, str, str] | None = None
        for path in candidates:
            v = info[path]
            for i, point in enumerate(resolved.points):
                a, b = base.get((i, path)), pred.get((i, path))
                if a is None or b is None or not (math.isfinite(a) and math.isfinite(b)):
                    continue
                try:
                    sigma = _sigma_of([a, b], v.unit or "1", v.pressure_reference)
                except InvalidValueError:
                    break
                sep = abs(b - a) / sigma
                if top is None or (sep, path) > (top[0], top[1]):
                    top = (sep, path, point.name)
        if top is not None and top[0] >= SUGGESTION_SEPARATION:
            out[f.ref] = (top[1], top[2], top[0])
    return out, evaluations


# ----------------------------------------------------------------------------------------
# notes
# ----------------------------------------------------------------------------------------
@dataclass
class _Summary:
    """What :func:`_notes` reports on."""

    ranked: Sequence[Hypothesis]
    best: Hypothesis
    baseline: Hypothesis
    runner: Hypothesis | None
    ambiguous: bool
    plausible: Sequence[str]
    unexplained: bool
    conclusion: str
    evidence: Sequence[_Evidence]
    suggestions: Sequence[SensorSuggestion]
    sightings: Mapping[str, tuple[str, str, float] | None]
    testable: Sequence[Hypothesis]
    singles: Sequence[Hypothesis]
    skipped: Mapping[str, str]
    max_faults: int
    include_leaks: bool
    fits: _Fits


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


def _and(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _notes(s: _Summary) -> list[str]:
    """Plain-language conclusions, most important first."""
    notes: list[str] = []
    best, base = s.best, s.baseline
    fit = f"chi-square {best.chi_square:.3g} for {best.measurements} measured values" + (
        f", p = {best.p_value:.2g}" if best.p_value is not None else ""
    )
    if s.unexplained:
        notes.append(_unexplained_note(s))
    # --- the conclusion --------------------------------------------------------------------
    if best is base:
        testable_singles = [h for h in s.testable if len(h.faults) == 1]
        closest = testable_singles[0] if testable_singles else None
        if not s.testable:
            text = (
                "No fault hypothesis could be tested with these measurements and operating "
                "points (see the notes on what was not tested or cannot be detected)."
            )
            if s.unexplained:
                text += f" The system as given is the only hypothesis scored ({fit})."
            else:
                text += (
                    f" The system as given fits the measurements ({fit}), but that says "
                    "nothing about the faults that were not tested."
                )
        elif s.unexplained:
            text = (
                "The closest of the tested hypotheses is the system as given, with no fault "
                f"({fit}): no testable fault hypothesis improves on it by more than the 2 AIC "
                "units that its fitted parameter costs."
            )
        else:
            text = (
                f"No fault is detected among the {len(s.testable)} testable fault hypotheses: "
                f"the system as given explains the measurements best ({fit})."
            )
            if s.runner is None:
                text += (
                    " No fault hypothesis improves the fit by more than the 2 AIC units that its "
                    "fitted parameter costs."
                )
        if closest is not None:
            text += f" The closest single fault is {_hypothesis_text(closest)}."
        notes.append(text)
    else:
        lead = "The closest of the tested hypotheses is" if s.unexplained else "Best explanation:"
        text = f"{lead} {_hypothesis_text(best)} ({fit})."
        text += f" It is {_aic_text(base.aic - best.aic)} AIC units better than no fault"
        if s.runner is not None and s.runner is not base:
            gap = _aic_text(s.runner.delta_aic)
            text += f" and {gap} better than the runner-up, {s.runner.name}"
        notes.append(text + ".")
        notes.extend(_evidence_notes(s))
    # --- blind spots, ambiguity ------------------------------------------------------------
    if s.sightings:
        notes.append(_undetectable_note(s))
    if s.ambiguous and s.runner is not None:
        shown = [h for h in s.ranked if h.name in s.plausible]
        weights = ", ".join(f"{h.name} {h.weight:.2f}" for h in shown[:6] if h.weight is not None)
        more = ", ..." if len(shown) > 6 else ""
        notes.append(
            f"Ambiguous: {len(shown)} hypotheses explain the measurements within "
            f"{AMBIGUITY_AIC:g} AIC units of the best, so the measurements cannot tell them "
            f"apart (Akaike weights: {weights}{more})."
        )
        notes.append(_suggestion_note(s))
    # --- the system as given and the fits --------------------------------------------------
    faulty = _not_healthy_note(s)
    if faulty:
        notes.append(faulty)
    for e in best.estimates.values():
        unit = _unit_suffix(e.unit)
        end = e.bound_end
        if end == "far":
            notes.append(
                f"{e.fault}: the fit sits at the {e.at_bound} end of its range ({e.value:g}"
                f"{unit}) and the data push further, so the fault may be larger than its "
                "range allows, or another fault is at work."
            )
        elif end == "start" and e.baseline is not None and e.baseline != e.healthy:
            pass  # reported with the system as given (_not_healthy_note)
        elif end == "start":
            notes.append(
                f"{e.fault}: the fit sits at its healthy value ({e.value:g}{unit}) and the data "
                "push past it, away from this fault: the measurements do not support "
                f"{e.fault}" + (" in this combination." if len(best.faults) > 1 else ".")
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
    if s.skipped:
        shown_skips = list(s.skipped.items())[:8]
        notes.append(
            "Not tested: "
            + "; ".join(f"{k}: {v}" for k, v in shown_skips)
            + (f"; and {len(s.skipped) - 8} more (see skipped)" if len(s.skipped) > 8 else "")
            + "."
        )
    return notes


def _unexplained_note(s: _Summary) -> str:
    best = s.best
    tries = []
    if not s.include_leaks:
        tries.append("include_leaks=True")
    if s.max_faults < MAX_FAULTS and len([h for h in s.testable if len(h.faults) == 1]) >= 2:
        tries.append(f"max_faults={MAX_FAULTS}")
    return (
        "Unexplained: even the best hypothesis misses the measurements by more than the "
        f"stated uncertainties explain (reduced chi-square {best.reduced_chi_square:.3g}, "
        f"p = {best.p_value:.2g}), so no conclusion is drawn: the fault may not be among the "
        "hypotheses"
        + (f" (try {' or '.join(tries)})" if tries else "")
        + ", the system as given may be wrong, or the uncertainties are too small, which also "
        "overstates every AIC difference (false_alarm is not computed). Read the largest "
        "residuals of the best hypothesis."
    )


def _evidence_notes(s: _Summary) -> list[str]:
    """The false-alarm notes of a best fault: against no fault, and against the smaller
    combination whose extra faults have the weakest evidence."""
    if not s.evidence:
        return []
    out: list[str] = []
    none = next(e for e in s.evidence if not e.against.faults)
    chance = (
        f"A healthy plant would give one of the {none.family} hypotheses tested this much "
        f"advantage over no fault by chance with probability at most {max(none.bound, 1e-15):.2g}"
    )
    if none.bound > FALSE_ALARM_LEVEL:
        out.append(
            f"The evidence for a fault is weak. {chance}; confirm with more measurements "
            "before acting."
        )
    else:
        out.append(chance + ".")
    weakest = s.evidence[0]
    if weakest.against.faults and weakest.bound > FALSE_ALARM_LEVEL:
        g, best = weakest.against, s.best
        extra = _and([r for r in best.faults if r not in g.faults])
        alone = f"{g.name} alone" + (" (fitted for this check)" if weakest.reference else "")
        gain = g.aic - best.aic
        if gain <= 0:
            body = (
                f"{alone} explains the measurements as well (AIC {g.aic:.4g} against "
                f"{best.aic:.4g}), so the data do not support {extra}"
            )
        else:
            body = (
                f"it improves on {alone} by only {_aic_text(gain)} AIC units, and noise alone "
                f"would give one of the {weakest.family} tested hypotheses that add to "
                f"{g.name} this much advantage with probability up to {weakest.bound:.2g}"
            )
        out.append(
            f"The evidence for {extra} in addition to {g.name} is weak: {body}. {g.name} "
            f"itself is {_aic_text(s.baseline.aic - g.aic)} AIC units better than no fault. "
            f"Confirm {extra} with more measurements before acting on it."
        )
    return out


def _undetectable_note(s: _Summary) -> str:
    refs = list(s.sightings)
    head = (
        f"Not detectable with these measurements: {_and(refs)}. No measured value responds to "
        f"{'it' if len(refs) == 1 else 'them'} (the fit's verdict is not_identifiable), so "
        f"the conclusion says nothing about {'it' if len(refs) == 1 else 'them'}."
    )
    seen = [
        f"{v[0]} for {ref} (a fault of half its range moves it by {v[2]:.2g} default "
        f"uncertainties at point '{v[1]}')"
        for ref, v in s.sightings.items()
        if v is not None
    ]
    unseen = [ref for ref, v in s.sightings.items() if v is None]
    tail = ""
    if seen:
        tail += " To test " + ("it" if len(refs) == 1 else "them") + ", measure "
        tail += "; ".join(seen) + "."
    if unseen:
        tail += f" No unmeasured variable would see {_and(unseen)} at these operating points"
        if any(ref.startswith(LEAK_PREFIX) for ref in unseen):
            tail += (
                " (where a supply or drain fixes the pressure, a leak changes nothing but that "
                "boundary's own flow)"
            )
        tail += "."
    return head + tail


def _suggestion_note(s: _Summary) -> str:
    if not s.suggestions:
        return (
            "No unmeasured variable separates the plausible hypotheses by one default "
            "uncertainty at these operating points; measure at other operating points "
            "(other settings) instead."
        )
    sug = s.suggestions[0]
    unit = _unit_suffix(sug.unit)
    also = [x.path for x in s.suggestions[1:4]]
    parts = []
    for g in sug.groups:
        vals = [sug.predictions[n] for n in g]
        lo, hi = min(vals), max(vals)
        shown = f"{lo:.4g}" if f"{lo:.4g}" == f"{hi:.4g}" else f"{lo:.4g} to {hi:.4g}"
        parts.append(f"{_and(list(g))} ({shown}{unit})")
    apart = f"at least {sug.separation:.2g} default uncertainties apart"
    if sug.resolves:
        text = (
            f"To resolve it, measure {sug.path}: at point '{sug.point}' the fits predict "
            + "; ".join(parts)
            + f", {apart}"
        )
    else:
        tied = [_and(list(g)) for g in sug.groups if len(g) > 1]
        split = (
            f"it separates {parts[0]} from {parts[1]}"
            if len(parts) == 2
            else f"it splits them into {len(parts)} groups: " + "; ".join(parts)
        )
        text = (
            f"To narrow it down, measure {sug.path}: at point '{sug.point}' {split} ({apart}), "
            f"which settles {sug.separated} of the {sug.pairs} pairs of plausible hypotheses; "
            + "; ".join(tied)
            + (" would remain tied" if len(tied) == 1 else " would each remain tied")
        )
    return (
        text
        + (f" (also: {', '.join(also)})" if also else "")
        + ". A refit that includes the new measurement can narrow the gap."
    )


def _not_healthy_note(s: _Summary) -> str:
    """The faults whose value in the system as given is not healthy (they are fitted only
    away from healthy), and those whose data push back toward healthy."""
    faults: dict[str, _Fault] = {}
    for fs in s.fits.faults_of.values():
        for f in fs:
            if f.baseline is not None and not f.relative and f.baseline != f.healthy:
                faults.setdefault(f.ref, f)
    if not faults:
        return ""
    listed = [
        f"{ref} ({f.path} = {f.baseline:g}{_unit_suffix(f.unit)}, healthy {f.healthy:g})"
        for ref, f in faults.items()
    ]
    text = (
        f"The system as given is not healthy in {_and(listed)}: such a fault is fitted only "
        "from there away from healthy, since a value nearer healthy would mean a repaired or "
        "better plant, not this fault."
    )
    back: list[str] = []
    for e in [*(h.estimates[h.faults[0]] for h in s.singles), *s.best.estimates.values()]:
        if e.fault in faults and e.bound_end == "start" and e.fault not in back:
            back.append(e.fault)
    if back:
        text += (
            f" The data push {_and(back)} back toward healthy: the system as given overstates "
            f"{'this fault' if len(back) == 1 else 'these faults'} (a repair, or an earlier "
            "calibration that no longer holds). Update the system (for example calibrate(..., "
            "apply=True)) and diagnose again."
        )
    return text
