# Operations benchmark: freeze records

Each entry records what a freeze commit fixes ([PREREGISTRATION.md section 7](PREREGISTRATION.md#7-freezes-sealing-and-firewalls)). The public files are fixed by the commit that adds the entry. The private folder `worldparts-opsbench` is fixed by:

- its full commit id and tree id;
- its **archive hash**: the SHA-256 of `git -c core.autocrlf=false archive --format=tar <commit id>`, made with git 2.54.0;
- its **content hash**: the full SHA-256 that `validation.seqval.code_hash()` computes over the files that decide what a task or its truth holds. The covered files and the SHA-256 of each are listed in a manifest file per freeze.

Logged fixes and orchestration changes after a freeze are dated entries below.

## freeze-0a, 2026-09-26 (before the harness pilot)

**Public:**

- this commit. It holds PREREGISTRATION.md (draft 3, binding from this commit), the grader, the harness, the v0.2 harness modules it imports, the headroom rule, `analysis/power.py`, the checklist, the preambles, the infrastructure rules and `uv.lock`;
- [bundles-dev.sha256](bundles-dev.sha256): the development bundles that exist now, which are ops-f1-003, ops-f4-001, ops-f4-002 and ops-f4-003, with realisations 1 to 3 each.
- [truth-dev.sha256](truth-dev.sha256): the validated development truths, the four below.
- [stage0-settings.json](stage0-settings.json): the session settings below, as `headroom` checks them.

**Private folder:**

- **Commit** `5a9cafded111f23955c2a51694ddaf0846e5b554`, tree `5094a4a9f23375b33b632153c98ff4a1199f399a`.
- **Archive hash** `5bd462fc3c4ab8103ac58f143b401fea92900ab417f6d8f07ffed0c8b4ba21a2`.
- **Content hash** `787451b432963c1fdff3caec11566719668c1faacec7016d85b790df9b7c8f52`, over the 60 files in [freeze-0a-private-content.sha256](freeze-0a-private-content.sha256). Its first 16 hex digits, `787451b432963c1f`, are what `code_hash()` returns and what the checkpoints are stamped with.

**Development truth and validity reports:**

| File | SHA-256 | Produced under |
|---|---|---|
| ops-f1-003.truth.json | `b47bf2fa371eb985dba376d229c936c8644b0e2663dd30145b8e708e5e064ec8` | content hash `3a7f7640127a537a` (see note 1) |
| ops-f1-003.meta.json | `9e343b22b87cbaf4faa95f85f3f16b1fdcbdb7008f98fc755bc163bda52efa6c` | as above |
| ops-f4-001.truth.json | `fc2100fb18efd729ba4685338b2cb8ba311ef5ff698c193c39f591a989b1a82f` | generator stage, 2026-09-25 (see note 2) |
| ops-f4-001.meta.json | `97d224fba4129a278ee1e5aef846064021e257d17c07c20a44c0b073f8db43db` | as above |
| ops-f4-002.truth.json | `2761b1d831ee73e691bf949f070ae4571d2df86232745c52465f783510db1ba7` | as above |
| ops-f4-002.meta.json | `87ed1fd3f0fe26550acff08969582aadc533ed21eabaa2c9dfe831d53028dadc` | as above |
| ops-f4-003.truth.json | `dd3ab47622f0157e535234b718e2a60c0bd420eed6cd07f4058d611e1776194b` | as above |
| ops-f4-003.meta.json | `9fb8d2ca604e426c08597a5f98bae9e694fa53b9452b0e4619c1101ef49a9154` | as above |

**Notes on the truth files:**

1. **ops-f1-003.** The only code change between `3a7f7640127a537a` and the freeze-0a content hash is the fix to the wear-table memo (private commit `30a5143`), which does not change any value. Six checkpointed oracle realisations recomputed under the new hash are identical to the stored rows.
2. **The three F4 truths** were written on 2026-09-25 by the generator stage's code, between private commits `ed75dd6` and `b6fbb48`, before the content hash existed.
3. **Regeneration check.** At freeze-0b, the freeze-0b code writes all four bundles, truth files and validity reports again (PREREGISTRATION.md section 7). Bundles must be byte-identical. A truth file or validity report that differs is replaced and recorded here with each difference and its cause. A task the freeze-0b code rejects is redrawn. The pilot's sessions on a changed task are superseded, and the pilot is not scored.

**Sessions.** Every Stage 0 session, including infrastructure re-runs, moved-realisation re-runs and replacements, uses exactly these settings. A session run otherwise is not scored.

- **CLI:** Claude Code 2.1.280, pinned at `%LOCALAPPDATA%\worldparts-bench\claude-cli-2.1.280\claude.exe` (SHA-256 `6d3f8ff8abf9fd562263240da0222e0bf40e3f0370d6e51c0a6f938e2c7c3f50`) and set with `WPBENCH_CLAUDE`.
- **Models,** called by their ids, which are the harness defaults: `claude-sonnet-5` and `claude-opus-5-5`.
- **Effort:** no `--effort` flag, so each model runs at its CLI default. The harness removes the parent's `CLAUDE*` variables (among them `CLAUDE_EFFORT`) and the model-behaviour variables of `runner.DROP_BEHAVIOUR`.
- **Budget cap:** none. **Limits:** 120 turns and 2,400 s per session. **Parallel sessions:** `--jobs 1`.
- **code+ environment:** `ops-code-plus-6b60722d37b5`, with `stamp.json` SHA-256 `7dfc14e70c41131a8c10fdb9cfa6a1b287da76a272d70afdb3610e37fbfcfc89`. The versions are those `harness code-env` prints.
- **Validation during sessions:** at most 3 worker processes at idle priority (`work/WORKERS` in the private folder).
- **Pilot:** at most 6 sessions on ops-f1-003 and ops-f4-001, in their own run directory, never passed to `grade` or `headroom` for Stage 0.

## Changes after freeze-0a

None yet.
