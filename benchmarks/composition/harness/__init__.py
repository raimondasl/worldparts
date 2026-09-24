"""Harness of the worldparts v0.2 composition benchmark (see benchmarks/README.md).

Modules: :mod:`.tasks` (task files and validation), :mod:`.reference` (reference
executor), :mod:`.regen` (recompute expected answers), :mod:`.grading` (answer format,
final-JSON parsing, scoring, traceability, stream parsing), :mod:`.runner` (Claude Code
CLI sessions), :mod:`.report` (summaries). The command line is ``python -m
benchmarks.composition.harness``.
"""
