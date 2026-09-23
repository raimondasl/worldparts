# Contributing to worldparts

Thanks for helping. worldparts is small on purpose: a manifest format, a tested catalogue
of water components, a narrow reference solver and an MCP server. Contributions that make
the catalogue more trustworthy for pumping, treatment and distribution work are the most
welcome: new components, better contracts, sharper warnings, and adapters that measure
divergence against other simulators. See the [roadmap](docs/roadmap.md).

## The design contract changes first

[docs/design.md](docs/design.md) is the architecture contract. It fixes the conventions,
the public APIs and the exact interface of every component. When code, manifests or docs
need to disagree with it, change the design first: open an issue or a pull request that
edits `docs/design.md`, agree on it, then change the code. A pull request that changes an
interface (a port, a variable name, a unit, a default, a warning code, a tool or a CLI
command) without a matching design change will be asked to add one.

## Development setup

You need [uv](https://docs.astral.sh/uv/). uv installs a suitable Python (3.11 or later)
and every dependency, including the optional `wntr` and the dev tools.

```sh
git clone https://github.com/raimondasl/worldparts
cd worldparts
uv sync
```

Always run tools through uv (`uv run ...`), so they use the project environment.

## Checks

Run all of these before you open a pull request. CI runs the tests and ruff on Linux and
Windows. The catalogue self-test also runs inside the tests (`tests/test_catalog.py`).

```sh
uv run pytest
uv run ruff check src tests examples tools
uv run ruff format --check src tests examples tools
uv run worldparts check-catalog
```

Useful narrower commands:

```sh
uv run pytest tests/test_catalog.py -k media_filter       # one component's catalogue tests
uv run worldparts check-catalog --component valve --verbose
uv run worldparts validate                                # schema and semantic checks of every manifest
uv run python tools/gen_catalog_docs.py                   # regenerate docs/catalog.md
uv run python tools/gen_catalog_docs.py --check           # exit 1 if docs/catalog.md is stale
```

`tests/test_docs.py` fails when `docs/catalog.md` is out of date. It also runs the Python
examples in `README.md` and compares their printed output with the output shown there. If
you change a manifest, regenerate the catalogue page. If you change behaviour the README
shows, update the README.

Never delete, skip or loosen a test to make a change pass. If an expectation is wrong,
show why (a hand calculation or an independent library) in the same change.

## Adding a component

A component is two files and no registry entry:

1. `src/worldparts/catalog/hydraulic/<alias>.yaml`: the manifest. The format is specified in
   [spec/component-manifest.md](spec/component-manifest.md), and the normative JSON Schema
   is `src/worldparts/schemas/component-manifest.schema.json`.
2. A class in `src/worldparts/components/<family>.py` that subclasses
   `worldparts.components.base.Component`. The manifest names it in
   `implementations.reference.python`.

[docs/authoring-components.md](docs/authoring-components.md) is the detailed guide to the
base API, the laws, the simulation order and a worked example. In short:

1. **Design section first.** Add the component to `docs/design.md` section 8: ports,
   parameters with units, defaults and hard limits, inputs, states, observables, modes,
   envelope and warnings, and the model equations.
2. **Manifest.** Declare everything the design section lists. Use SI-convertible units from
   the vocabulary, `pressure_reference: difference` for pressure drops and
   `quantity: temperature_difference` for temperature rises. Write the description for an
   agent deciding whether the model fits its question, including what it does not model.
   Record provenance: the real sources of the equations, and `acquisition: generic` or
   `estimate` for illustrative defaults. Never present defaults as a specific product.
3. **Implementation.** Build the internal network in `build()`, push coefficients in
   `update_laws()` and return every observable (SI, or `None` when undefined) from
   `observables()`. Only use the monotone laws in `worldparts.laws`. Declare every warning
   code the code emits under `warnings`.
4. **Scenarios.** At least two small systems with the component named `dut`. **Expected
   numbers come from hand calculations or an independent library, never from running
   your own model.** Show the calculation in the scenario description, as the pump's
   `operating-point` scenario does.
5. **Contracts.** At least three, including an `equal` check of mass conservation
   (`dut.<in>.m_flow` equals minus `dut.<out>.m_flow`). Good contracts state physics that
   must hold everywhere in a sweep: monotonic responses, a law checked against its
   hand-derived formula, and a `warning_iff` for every envelope rule.
6. **Tests.** `tests/test_catalog.py` picks up the manifest automatically. Add
   family-specific physics tests with hand calculations in `tests/test_<family>.py`.
7. **Docs.** Regenerate `docs/catalog.md` and add the component to the README catalogue
   table.

### A contract that cannot fail is a bug

A contract is only worth something if a wrong implementation breaks it. Before you submit,
break your implementation on purpose (a mutation) and check that the scenarios and
contracts catch it: flip a sign, drop a term, ignore an input or swap two ports. If nothing
fails, the contract is vacuous. Tighten it or add one that would have caught the mutation.

For example, making the valve's law ignore its position (so it always passes the full Kv)
fails two of its scenarios and three of its contracts: `flow-increases-with-opening`,
`kv-law` and `equal-percentage-monotonic`. Mass conservation still holds, as it should,
which is why mass conservation alone is never enough.

The contract runner helps here: a `warning` expectation or `warning_iff` check that names an
undeclared code fails instead of passing vacuously, and so does a check expression with a
misspelled name.

## Code style

- Python 3.11+, type hints on public functions, docstrings on public classes and functions.
- `ruff` enforces formatting and lint (line length 100). Run the check commands above.
- SI inside components and the solver; declared units only at the API boundary.
- Error messages are written for an agent: name the offending path or value and list the
  valid alternatives.
- No network access at runtime and no global state beyond the MCP server's system store.

## Commits and pull requests

- One logical change per commit. The subject is in the imperative mood and says what the
  commit does, in about 72 characters or fewer, for example `Add check valve with Kv law and
  leakage` or `Fix tank overflow rate in steady solves`.
- The body explains why, and gives the evidence for any change to physics or to an
  expected number: the hand calculation, the reference or the failing case.
- Keep the design contract, the manifest, the implementation, the tests and the generated
  docs consistent within a pull request.
- Add a line to [CHANGELOG.md](CHANGELOG.md) under "Unreleased" for anything a user would
  notice.

## Reporting issues

Report issues at <https://github.com/raimondasl/worldparts/issues>. For a wrong result,
include the system document (`get_system` or `System.to_dict()`), the value you expected
and how you got it.

## Licence

The code and manifests are licensed under Apache-2.0 ([LICENSE](LICENSE)). The licence
of each manifest's parameter data is its `provenance.data_license`, which is CC0-1.0
throughout the v0.1 catalogue.
