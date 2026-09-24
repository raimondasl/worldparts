"""Control loops: PI and hysteresis rules in a system (design section 13.1).

Controls are system-level rules, like EPANET controls: each reads one reported numeric
variable (``measure``) and writes one numeric input of a component (``actuate``). They are
not components, because ports are fluid-only. A system document lists them under an
optional top-level ``controls`` key::

    controls:
      - {name: duty_pressure, type: pi, measure: pump.outlet.p, setpoint: 4.0,
         actuate: pump.speed, gain: 0.2, integral_time: 10 s, output_min: 0.3,
         output_max: 1.2, direction: reverse}
      - {name: tower_level_switch, type: hysteresis, measure: tower.level,
         actuate: pump.speed, on_below: 1.0, off_above: 3.0, on_value: 1.0,
         off_value: 0.0, initial: on}

This module holds the definitions (:class:`Control`), their validation against a system's
variables (:func:`resolve_control`) and the per-step control laws (:class:`PIRuntime`,
:class:`HysteresisRuntime`). :class:`worldparts.System` drives them: ``add_control``,
``remove_control`` and ``controls``; ``check()`` reports ``unknown_variable``,
``invalid_control`` and ``control_conflict``; ``solve()`` goal-seeks every PI actuator and
holds every hysteresis state; ``simulate()`` runs the loops as sampled-data controllers.

Units: setpoints and thresholds are in the measured variable's display unit (or strings with
units); gains are actuator units per measured unit; outputs and output limits are in the
actuated input's declared unit.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from worldparts.errors import (
    InvalidValueError,
    UnknownVariableError,
    WorldpartsError,
    format_choices,
)
from worldparts.results import ComponentWarning, Issue, VariableInfo
from worldparts.units import parse_duration, parse_value

__all__ = [
    "CONTROL_TYPES",
    "CONTROL_WARNINGS",
    "Control",
    "ControlConflictError",
    "HysteresisRuntime",
    "InvalidControlError",
    "PIRuntime",
    "ResolvedHysteresis",
    "ResolvedPI",
    "resolve_control",
]

CONTROL_TYPES = ("pi", "hysteresis")

#: Warning codes raised for a control (component name ``control.<name>``): severity and
#: meaning.
CONTROL_WARNINGS: dict[str, tuple[str, str]] = {
    "control_saturated": (
        "warning",
        "The PI loop sits at an output limit with a non-zero error: the setpoint cannot be "
        "reached inside [output_min, output_max].",
    ),
    "short_cycling": (
        "warning",
        "The hysteresis control switches more than max_switches_per_hour times in a sliding "
        "hour; widen the band or add storage.",
    ),
    "control_direction": (
        "warning",
        "The PI loop acts the wrong way: the error grows as the output moves towards the "
        "setpoint, so a real loop would run to a limit. Check 'direction' (reverse raises the "
        "output when the measure is below the setpoint).",
    ),
    "control_unresponsive": (
        "warning",
        "In the steady solve the measure does not change with the actuator (for example a "
        "tank level, which solve() holds), so the loop cannot settle it there; the output is "
        "held at its current value. Simulate to see the loop act.",
    ),
    "control_not_converged": (
        "warning",
        "The steady solve of interacting PI loops did not settle within 50 rounds; the "
        "reported operating point may not meet every setpoint.",
    ),
}

#: Required and optional fields per control type (besides name and type).
_REQUIRED = {
    "pi": ("measure", "actuate", "setpoint", "gain", "integral_time"),
    "hysteresis": ("measure", "actuate", "on_below", "off_above", "on_value", "off_value"),
}
_OPTIONAL = {
    "pi": ("output_min", "output_max", "direction"),
    "hysteresis": ("initial", "max_switches_per_hour", "state"),
}
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Relative tolerance on the output range for "at a limit".
LIMIT_TOL = 1e-9
#: Steady solve: loops have converged when every actuator moves less than this fraction of
#: its range in a round (design 13.1), within at most :data:`MAX_ROUNDS` rounds.
ROUND_TOL = 1e-6
MAX_ROUNDS = 50
#: Sliding window of the short-cycling check, s.
CYCLE_WINDOW = 3600.0


class InvalidControlError(InvalidValueError):
    """An inconsistent control definition (``check()`` code ``invalid_control``)."""

    code = "invalid_control"


class ControlConflictError(InvalidValueError):
    """Two controls write the same input (``check()`` code ``control_conflict``)."""

    code = "control_conflict"


def _error_class(code: str) -> type[WorldpartsError]:
    return {
        "unknown_variable": UnknownVariableError,
        "control_conflict": ControlConflictError,
    }.get(code, InvalidControlError)


def raise_issue(issue: Issue) -> None:
    """Raise the error class matching an issue's code, with its message."""
    raise _error_class(issue.code)(issue.message)


@dataclass
class Control:
    """A control definition as written in a system document, plus its switch state.

    Attributes:
        name: Identifier, unique among the system's controls.
        type: ``pi`` or ``hysteresis``.
        fields: Every other document field as given (numbers or strings with units), so
            the document round-trips exactly.
        state: Hysteresis only: the current switch state, ``on`` or ``off`` (starts at
            ``initial``, default ``off``); changed by simulations and written to the
            document as ``state`` when it differs from ``initial``.
    """

    name: str
    type: str
    fields: dict[str, Any] = field(default_factory=dict)
    state: str | None = None

    @classmethod
    def from_document(cls, entry: Mapping[str, Any]) -> Control:
        """A control from a document entry ``{name, type, ...}`` (no validation)."""
        data = dict(entry)
        name = str(data.pop("name", ""))
        type_ = str(data.pop("type", ""))
        if type_ != "hysteresis":
            # 'state' belongs to hysteresis controls; elsewhere it stays a field, so
            # check() reports it as unknown.
            return cls(name, type_, copy.deepcopy(data))
        state = data.pop("state", None)
        ctrl = cls(name, type_, copy.deepcopy(data))
        ctrl.state = on_off(state) or ctrl.initial_state
        if state is not None and on_off(state) is None:
            ctrl.fields["state"] = state  # kept so check() reports it
        return ctrl

    @property
    def path(self) -> str:
        """``control.<name>``: the component name of this control's warnings."""
        return f"control.{self.name}"

    @property
    def measure(self) -> str:
        """The measured path as written."""
        return str(self.fields.get("measure", ""))

    @property
    def actuate(self) -> str:
        """The actuated input path as written."""
        return str(self.fields.get("actuate", ""))

    @property
    def initial_state(self) -> str:
        """Hysteresis: the ``initial`` field (default ``off``)."""
        return on_off(self.fields.get("initial", "off")) or "off"

    def reset(self) -> None:
        """Put a hysteresis control back to its ``initial`` state."""
        if self.type == "hysteresis":
            self.state = self.initial_state

    def to_dict(self) -> dict[str, Any]:
        """The document entry: name, type, the fields as given and a changed ``state``."""
        out: dict[str, Any] = {"name": self.name, "type": self.type}
        out.update(copy.deepcopy(self.fields))
        if self.type == "hysteresis" and self.state not in (None, self.initial_state):
            out["state"] = self.state
        return out

    def describe(self) -> str:
        """One line for listings: ``pi: pump.outlet.p -> pump.speed``."""
        return f"{self.type}: {self.measure} -> {self.actuate}"


@dataclass(frozen=True)
class ResolvedPI:
    """A validated PI control in display units (see :func:`resolve_control`)."""

    name: str
    measure: str
    actuate: str
    setpoint: float
    gain: float
    integral_time: float
    output_min: float
    output_max: float
    sign: float  # +1 reverse (e = setpoint - measure), -1 direct
    measure_unit: str
    measure_reference: str | None
    actuate_unit: str

    type = "pi"

    def error(self, measured: float) -> float:
        """The loop error: positive means the output should rise."""
        return self.sign * (self.setpoint - measured)

    def clamp(self, u: float) -> float:
        """``u`` limited to ``[output_min, output_max]``."""
        return min(max(u, self.output_min), self.output_max)

    def error_tol(self) -> float:
        """What counts as a non-zero error (measured unit)."""
        return 1e-6 * max(abs(self.setpoint), 1.0)

    def at_limit(self, u: float, e: float) -> bool:
        """True when ``u`` sits at a limit and the error pushes it further out."""
        span = self.output_max - self.output_min
        tol = LIMIT_TOL * span
        if e > self.error_tol():
            return u >= self.output_max - tol
        if e < -self.error_tol():
            return u <= self.output_min + tol
        return False


@dataclass(frozen=True)
class ResolvedHysteresis:
    """A validated hysteresis control in display units (see :func:`resolve_control`)."""

    name: str
    measure: str
    actuate: str
    on_below: float
    off_above: float
    on_value: float
    off_value: float
    max_switches_per_hour: float
    measure_unit: str
    measure_reference: str | None
    actuate_unit: str

    type = "hysteresis"

    def output(self, state: str | None) -> float:
        """The actuator value for a switch state."""
        return self.on_value if state == "on" else self.off_value

    def next_state(self, state: str | None, measured: float | None) -> str:
        """On below ``on_below``, off above ``off_above``, else hold."""
        current = state if state in ("on", "off") else "off"
        if measured is None:
            return current
        if measured < self.on_below:
            return "on"
        if measured > self.off_above:
            return "off"
        return current


Resolved = ResolvedPI | ResolvedHysteresis


def resolve_control(
    ctrl: Control, variables: Mapping[str, VariableInfo]
) -> tuple[Resolved | None, list[Issue]]:
    """Validate a control against a system's component variables.

    Args:
        ctrl: The definition.
        variables: Component variable path to its :class:`VariableInfo`.

    Returns:
        The resolved control (None when there are errors) and the issues, with codes
        ``unknown_variable`` (a ``measure`` or ``actuate`` path that does not exist) and
        ``invalid_control`` (anything else inconsistent).
    """
    where = ctrl.path
    issues: list[Issue] = []

    def bad(message: str, code: str = "invalid_control") -> None:
        issues.append(Issue("error", code, f"Control '{ctrl.name}': {message}", where))

    if not _NAME_RE.match(ctrl.name):
        bad("the name must be an identifier (letters, digits, underscores).")
    if ctrl.type not in CONTROL_TYPES:
        bad(f"unknown type '{ctrl.type}'; use 'pi' or 'hysteresis'.")
        return None, issues
    allowed = set(_REQUIRED[ctrl.type]) | set(_OPTIONAL[ctrl.type])
    for key in ctrl.fields:
        if key not in allowed:
            bad(f"unknown field '{key}' for type {ctrl.type}. " + format_choices(key, allowed))
    missing = [k for k in _REQUIRED[ctrl.type] if ctrl.fields.get(k) is None]
    if missing:
        bad(
            "missing "
            + ", ".join(missing)
            + f"; a {ctrl.type} control needs "
            + ", ".join(_REQUIRED[ctrl.type])
            + "."
        )
        return None, issues

    reported = {p: v for p, v in variables.items() if v.reported}
    m_info = reported.get(ctrl.measure)
    if m_info is None:
        bad(
            f"unknown measure '{ctrl.measure}'. "
            + format_choices(ctrl.measure, [p for p, v in reported.items() if _numeric(v)]),
            "unknown_variable",
        )
    elif not _numeric(m_info):
        bad(f"measure '{ctrl.measure}' is not numeric.")
        m_info = None
    a_info = variables.get(ctrl.actuate)
    if a_info is None:
        inputs = [p for p, v in variables.items() if v.kind == "input"]
        bad(
            f"unknown actuate '{ctrl.actuate}'. " + format_choices(ctrl.actuate, inputs),
            "unknown_variable",
        )
    elif a_info.kind != "input" or not _numeric(a_info):
        inputs = [p for p, v in variables.items() if v.kind == "input"]
        bad(
            f"actuate '{ctrl.actuate}' is a {a_info.kind}, not a numeric input; controls "
            "write component inputs. " + format_choices(ctrl.actuate, inputs)
        )
        a_info = None
    if m_info is None or a_info is None:
        return None, issues

    m_unit = m_info.unit or "1"
    m_ref = m_info.pressure_reference
    a_unit = a_info.unit or "1"
    lo_lim = -math.inf if a_info.minimum is None else a_info.minimum
    hi_lim = math.inf if a_info.maximum is None else a_info.maximum

    def finite(key: str, value: float) -> float | None:
        if not math.isfinite(value):
            bad(f"{key} must be a finite number, got {value!r}.")
            return None
        return value

    def measured_value(key: str) -> float | None:
        try:
            value = parse_value(ctrl.fields[key], m_unit, f"{where}.{key}", m_ref)
        except InvalidValueError as exc:
            bad(str(exc))
            return None
        return finite(key, value)

    def output_value(key: str, default: float | None = None) -> float | None:
        raw = ctrl.fields.get(key)
        if raw is None:
            if default is None or not math.isfinite(default):
                bad(f"{key} is required: {ctrl.actuate} has no hard limit to default to.")
                return None
            return default
        try:
            value = parse_value(raw, a_unit, f"{where}.{key}")
        except InvalidValueError as exc:
            bad(str(exc))
            return None
        if finite(key, value) is None:
            return None
        if not lo_lim <= value <= hi_lim:
            bad(
                f"{key} = {value:g} {a_unit} is outside the limits of {ctrl.actuate} "
                f"[{lo_lim:g}, {hi_lim:g}]."
            )
            return None
        return value

    def plain_number(key: str) -> float | None:
        raw = ctrl.fields.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            bad(f"{key} must be a number, got {raw!r}.")
            return None
        return float(raw)

    if ctrl.type == "pi":
        setpoint = measured_value("setpoint")
        gain = plain_number("gain")
        if gain is not None and gain <= 0:
            bad(
                f"gain must be positive (got {gain:g}); set direction to 'direct' or "
                "'reverse' for the sign of the action."
            )
        try:
            ti: float | None = parse_duration(
                ctrl.fields["integral_time"], f"{where}.integral_time"
            )
            if ti is not None and not 0 < ti < math.inf:
                bad(f"integral_time must be positive and finite, got {ti!r} s.")
                ti = None
        except InvalidValueError as exc:
            bad(str(exc))
            ti = None
        u_min = output_value("output_min", lo_lim)
        u_max = output_value("output_max", hi_lim)
        if u_min is not None and u_max is not None and not u_min < u_max:
            bad(f"output_min ({u_min:g}) must be below output_max ({u_max:g}).")
        direction = ctrl.fields.get("direction", "reverse")
        if direction not in ("reverse", "direct"):
            bad(
                f"direction must be 'reverse' (raise the output when the measure is below the "
                f"setpoint) or 'direct', got {direction!r}."
            )
        if issues:
            return None, issues
        assert setpoint is not None and gain is not None and ti is not None
        assert u_min is not None and u_max is not None
        return (
            ResolvedPI(
                ctrl.name,
                ctrl.measure,
                ctrl.actuate,
                setpoint,
                gain,
                ti,
                u_min,
                u_max,
                1.0 if direction == "reverse" else -1.0,
                m_unit,
                m_ref,
                a_unit,
            ),
            issues,
        )

    on_below = measured_value("on_below")
    off_above = measured_value("off_above")
    if on_below is not None and off_above is not None and not on_below < off_above:
        bad(
            f"on_below ({on_below:g}) must be below off_above ({off_above:g}) "
            f"{m_unit}: the band between them is the hysteresis."
        )
    on_value = output_value("on_value")
    off_value = output_value("off_value")
    if on_value is not None and off_value is not None and on_value == off_value:
        bad("on_value and off_value are equal, so the control would never change anything.")
    for key in ("initial", "state"):
        if key in ctrl.fields and on_off(ctrl.fields[key]) is None:
            bad(f"{key} must be 'on' or 'off', got {ctrl.fields[key]!r}.")
    limit = 6.0
    if "max_switches_per_hour" in ctrl.fields:
        value = plain_number("max_switches_per_hour")
        if value is not None and value <= 0:
            bad("max_switches_per_hour must be positive.")
        limit = value if value is not None else limit
    if issues:
        return None, issues
    assert on_below is not None and off_above is not None
    assert on_value is not None and off_value is not None
    return (
        ResolvedHysteresis(
            ctrl.name,
            ctrl.measure,
            ctrl.actuate,
            on_below,
            off_above,
            on_value,
            off_value,
            limit,
            m_unit,
            m_ref,
            a_unit,
        ),
        issues,
    )


def on_off(value: Any) -> str | None:
    """``'on'`` or ``'off'`` for a switch state (YAML reads a bare on/off as a boolean)."""
    if value is True or value == "on":
        return "on"
    if value is False or value == "off":
        return "off"
    return None


def _numeric(info: VariableInfo) -> bool:
    return info.type in ("number", "integer")


def control_warning(name: str, code: str, detail: str = "") -> ComponentWarning:
    """A warning of control ``name`` (component ``control.<name>``)."""
    severity, message = CONTROL_WARNINGS[code]
    return ComponentWarning(f"control.{name}", code, severity, (detail + " " + message).strip())


@dataclass
class PIRuntime:
    """Sampled-data state of a PI loop during one simulation.

    The incremental (velocity) form ``u += gain * (e - e_prev) + gain * dt / integral_time
    * e`` works on the output itself, so clamping ``u`` to the output limits is the
    anti-windup: at a limit the integral stops, and the output leaves the limit at the
    first sample at which the error changes sign.
    """

    spec: ResolvedPI
    output: float
    e_prev: float | None = None
    t_prev: float | None = None

    def update(self, t: float, measured: float | None) -> tuple[float, float | None]:
        """New output from the value measured at ``t``; returns ``(output, error)``.

        The first sample has no previous error or time, so it only initialises them (a
        bumpless start from the actuator's current value). An undefined measurement holds
        the output.
        """
        if measured is None:
            return self.output, None
        e = self.spec.error(measured)
        if self.e_prev is not None and self.t_prev is not None:
            dt = t - self.t_prev
            k, ti = self.spec.gain, self.spec.integral_time
            du = k * (e - self.e_prev) + k * dt / ti * e
            self.output = self.spec.clamp(self.output + du)
        self.e_prev = e
        self.t_prev = t
        return self.output, e


@dataclass
class HysteresisRuntime:
    """Switch history of a hysteresis control during one simulation."""

    spec: ResolvedHysteresis
    control: Control
    switch_times: list[float] = field(default_factory=list)

    def update(self, t: float, measured: float | None) -> bool:
        """Switch on the value measured at ``t``; returns True when the state changed."""
        new = self.spec.next_state(self.control.state, measured)
        changed = new != self.control.state
        if changed:
            self.control.state = new
            self.switch_times.append(t)
        return changed

    def switches_in_window(self, t: float) -> int:
        """Switches in the sliding hour ``(t - 3600 s, t]``."""
        start = t - CYCLE_WINDOW + 1e-9
        return sum(1 for s in self.switch_times if start < s <= t + 1e-9)
