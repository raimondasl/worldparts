"""Harness of the worldparts operations benchmark (v0.3, see ../PREREGISTRATION.md).

Modules:

- :mod:`.bundles`: task bundles and ``task.json`` (``$WPBENCH_OPS_BUNDLES``), truth files
  (``$WPBENCH_OPS_TRUTH``) and the SHA-256 manifest of a bundle set;
- :mod:`.arms`: the arms, their preambles (``../preambles/``) and the session prompt;
- :mod:`.quickref`: the quick reference of the lib arms and section 3's limits on it;
- :mod:`.toolkit`: the ``code-skill`` toolkit (``$WPBENCH_OPS_TOOLKIT``), copied into its
  sessions as ``reference/``;
- :mod:`.env`: the ``code-plus`` Python environment and the lib environment (code-plus
  plus a worldparts wheel), each keyed by a hash of what it holds;
- :mod:`.runner`: headless ``claude -p`` sessions, one fresh working directory each, and
  the run directory layout (opaque session ids, attempts);
- :mod:`.infra`: the pre-registered infrastructure-error signatures and the blind audit;
- :mod:`.grading`: the grader for every answer kind and the diagnosis table (section 6.6);
- :mod:`.score`: scoring of scripted answers, in full (owner) or pass/fail (builders);
- :mod:`.firewall`: the firewall audit of builder transcripts (section 7);
- :mod:`.markers`: contamination markers;
- :mod:`.headroom`: the Stage 0 headroom rule (section 8);
- :mod:`.report`: run summaries.

The command line is ``python -m benchmarks.operations.harness`` with the subcommands
``run``, ``audit``, ``grade``, ``headroom``, ``report``, ``score``, ``firewall-audit``,
``manifest``, ``code-env`` and ``lib-env``. It reuses the v0.2 composition harness
(:mod:`benchmarks.composition.harness`) for the session isolation, the environment
scrubbing, the stream parser, the final-JSON parser, the worldparts wheel build and the
v0.2 contamination markers. Builders use ``tools/ops_passfail.py``.
"""
