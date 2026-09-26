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
- **Session shell** (added by the logged fix below): the private Git Bash `git-bash-6f0c4145d65f`, whose `/tmp` is `%LOCALAPPDATA%\worldparts-bench\session-tmp`.

## freeze-0b, 2026-09-26 (before Stage 0)

**Public:**

- this commit;
- [bundles-dev.sha256](bundles-dev.sha256): realisation 1 of all 16 development tasks, and realisations 2 and 3 of the five accepted tasks;
- [truth-dev.sha256](truth-dev.sha256): the validated truths, ops-f1-001, ops-f1-003, ops-f4-001, ops-f4-002 and ops-f4-003. Each is produced under content hash `ed64b3e257623db0` and passes the public truth checks.

**Private folder:**

- **Commit** `0bc65c2ef1edf35cc7ecbce6b2ae3ff3e3743ebf`, tree `b7bb8e0c99bdab7abd6a6a9a3ddade7ef1df145a`.
- **Archive hash** `71bac2b44b42074c57d7fdee994fa083793a576163cd9e112dfc2f6f7ef67df1`.
- **Content hash** `ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b`, over the 60 files in [freeze-0b-private-content.sha256](freeze-0b-private-content.sha256).

**Content-hash check against freeze-0a (`787451b432963c1f`).** The only covered file that changed is `validation/seqval.py`, for the change section 7 allows before freeze-0b: R2 on realisations 1 to 3 wherever neither R-a nor R-b passes (6.5).

- **Checkpoints.** Existing checkpoint rows were verified bit for bit under the new code: 20 rows of oracle, R-a, R-b and naive on ops-f1-001, ops-f1-002, ops-f1-003, ops-f2-003 and ops-f3-004. The checkpoints were then migrated to the new hash, with the record kept in each checkpoint.
- **Allowed change: realisation 1 at the screen.** Writing realisation 1 as soon as a draw passes its screen, the other change section 7 allows, touches only files outside the content hash.

**Private commits after freeze-0a:**

| Commits | Change | Kind |
|---|---|---|
| `a992d24`, `c302cbf` | Courtesy-mode review | Orchestration, logged above |
| `2c0441f`, `c501e7e`, `bfa87f1`, `04c64d1` | The two allowed changes, their review and the tooling (regeneration check, public-updates listing, checkpoint verification and migration) | Allowed changes and tooling |
| `6e0ff5d` | The regeneration tool records the cause of one meta difference | Tooling |
| `dc7f9c9` | The private folder's stop check runs its fast tests only | Tooling |
| `34602a4`, `9f492dc`, `0bc65c2` | The bundle writers follow INTERFACE.md | Logged fix, entry below |

**Regeneration check.** The freeze-0b code wrote the four freeze-0a tasks again ([freeze-0b-regen-install.md](freeze-0b-regen-install.md)):

- **Bundles.** All 12 bundles (r1 to r3 of ops-f1-003 and ops-f4-001 to 003) are byte-identical.
- **Truth files.** Each differs only by the new top-level field `r2_applies: true`. No answer, tolerance or reference result changed.
- **Validity reports.** They gain `content_hash`, `content_hash_full`, `r1_moved_from`, `screen_passed`, R2's record on realisations 1 to 3 and `n_max`. Their `n_real` changes from 200 to null on the F4 tasks, because an F4 validation has no sequential stop; the Monte Carlo size is now `n_max`. Times and paths change too.
- **Installed.** The new files replace the old ones, which are kept in the private `truth/dev_superseded/freeze-0a/`.
- **Pilot unaffected.** No pilot session's task changed.

**Slot order (section 8).** Slot order follows the development table of section 4 cell by cell. Within a cell, it is the order in which the draws passed the screen. Before freeze-0b, draws were rejected at the screen or by validation and redrawn within their cells under 6.5: ops-f1-001, ops-f1-002, ops-f2-001, ops-f2-002, ops-f3-001 and ops-f3-005, as the private attempt logs record.

| # | Slot | Cell | Attempt | State at freeze-0b | r1 digest |
|---:|---|---|---:|---|---|
| 1 | ops-f1-001 | F1/G-ind | 1 | accepted | `a8b0d563d03ab4a2396b79db3fbca70f6969d36939d228871c0ab2bd1224efd0` |
| 2 | ops-f1-002 | F1/G-ind | 1 | validating | `b2959d649dea6c346fe9bc76446cbbda2cba54ef03c256f04c21265d7f7d8db5` |
| 3 | ops-f1-003 | F1/G-epa | 0 | accepted | `2eef78fb13eff0e86ff5294c82150fcc72a5f434f2f01b26dd25e5f7e39129c7` |
| 4 | ops-f1-004 | F1/G-epa | 0 | validating | `b04552471c6db1a38d2b4dd03457bd7438e7b12134720cd12b71b1f755017dce` |
| 5 | ops-f2-001 | F2/G-ind | 1 | validating | `4e3933b19dbee6fb8a1f7e6b07649e04c2c23279d8868aea8b4c9dd7d58b283b` |
| 6 | ops-f2-003 | F2/G-ind | 0 | validating | `fc48985bb1fb5e8f0e99e6dcb9f1314378177077abf6d6d465da3fab3fa5201d` |
| 7 | ops-f2-002 | F2/G-ind | 1 | validating | `e593be5fbc72f7069d6bae9559ef0e4f18682d859db46fd59d1f0bd9d9c0f24d` |
| 8 | ops-f3-001 | F3/G-ind | 1 | validating | `32fe12d245ab9003eac7f3126ca0254b3a4b7efee2a490d787b21bf5c30b0fa4` |
| 9 | ops-f3-002 | F3/G-ind | 0 | validating | `02ac89cf077e3bbd9bc6d40f38a6c79bc5756dfa284c944cf4fdd44a17d9909e` |
| 10 | ops-f3-003 | F3/G-ind | 0 | validating | `d1defa84a518630289a7fdc371b6497dcbc06f62155b58d120000e5e007495bf` |
| 11 | ops-f3-006 | F3/G-ind | 0 | validating | `e43dd6cc290c2b1104b608364a8149c16aecd1d0d6860a31ba1d916591889a0f` |
| 12 | ops-f3-004 | F3/G-epa | 0 | validating | `68060337da0398153b4cfbaa88f6d1e91d18e210fa61560b90d914fff1a0db0e` |
| 13 | ops-f3-005 | F3/G-epa | 2 | validating | `f9f87cd9c22feecb90088206e4159d69eb629cc7d7f2c44b75b56fd24bc9caa1` |
| 14 | ops-f4-003 | F4/G-ind | 3 | accepted | `042a323f960f731be1ecc1a3ad4b854dc33f918e24d8d0e30a177fef095fe2a8` |
| 15 | ops-f4-002 | F4/G-ind | 1 | accepted | `fc03b6bfaa0161a8393176da85b697fc69cd05f32f891452f176ffeeb455b70e` |
| 16 | ops-f4-001 | F4/G-epa | 1 | accepted | `1c888dd3eac01c54a785402e6b8ad7f1cf3875026d3783296eaf3497ef14b54e` |

**Stage 0 settings.** Sessions use the settings above and `stage0-settings.json`. While sessions run, validation uses at most 3 worker processes at idle priority (`work/WORKERS` = 3 in the private folder).

## Changes after freeze-0a

- **2026-09-26, pilot run.** The pilot ran 6 sessions from the frozen worktree at `15e8810`, in `benchmarks/operations/results/pilot-0a`:
  - ops-f1-003 in `code+` and `code-hint`, and ops-f4-001 in `code-hint`, on both models;
  - 2 to 3 minutes and $0.27 to $0.49 per session, $2.17 in all;
  - no infrastructure flags.

  The pilot is not scored.
- **2026-09-26, logged fix (isolation), found by the pilot.** Claude Code runs a session's shell commands in Git Bash. Git's mount table maps `/tmp` to the user's temporary folder, which all sessions share and which does not follow `TEMP`.
  - **What the pilot found.** One pilot session (ops-f1-003, `code+`, Sonnet 5) wrote its scripts to `/tmp/a.py` and appended to `/tmp/f.py`. A later session could read those files, or append to them.
  - **The fix.** Sessions now run with a private copy of Git Bash (`harness/session_bash.py`), whose `/tmp` is a benchmark-only folder. `TEMP`, `TMP` and `TMPDIR` point there too.
  - **Around each session.** The harness empties that folder before the session and moves what the session left there into its attempt folder. The shell's key is pinned in `run.json` and in `stage0-settings.json`.
  - **Sessions run one at a time.** The harness refuses `--jobs` other than 1 while the private shell is used.
  - **Pilot leftovers.** The pilot session's seven leftover files were moved from the user's temporary folder into its attempt folder (`tmp-leftovers/`). The only other pilot session on that task wrote into its own working directory and never named `/tmp`, so nothing passed between pilot sessions.
  - **Scope.** No grader, checklist, preamble or rule changed.
- **2026-09-26, private orchestration.** Two private commits review the scheduler's courtesy mode: `a992d24` and `c302cbfcc26ddb17d2c9ded2415b1f1521c4d690`.
  - **What the mode does.** At most 6 workers run, at idle priority. While the owner uses the PC, one worker runs and the others are frozen.
  - **Files touched.** `generators/scheduler.py`, `generators/__main__.py`, `generators/status.py`, their tests, and appendix entry A6.14, which documents them.
  - **Content hash.** Unchanged, `787451b432963c1f`.
- **2026-09-26, logged fix (loader) and logged fixes of the private writers: the task.json and truth.json formats.**
  - **What was found.** An audit loaded the private generator's task.json and truth.json, as its writers produce them, through the loader and the grader. The loader refused every F2 and every F3 development bundle:
    - The F2 keys were named `hours_to_trigger_F-1` and so on. The loader requires lower-case snake_case keys, and the grader's key normalisation relies on that rule.
    - In the F3 vocabulary, both sensor faults, `pressure_sensor_fault` and `flow_sensor_fault`, had text bounds ("2 x stated accuracy", "+-6 x stated accuracy"), and `reverse_rotation` (no magnitude) had a null `m_min` and `range`. INTERFACE.md said nothing about a fault without numeric bounds.
    - The audit also found that F3 tickets did not state the sign of a sensor-fault magnitude, or that faults are named without their instrument. The grader follows 6.6 in both cases.
  - **Private fixes** are logged bug fixes of the writers, on branch `interface-conformance`:
    - commit `34602a4ef3b38ca4c490aa4882fff96c15fa303d` (fixes and tests), commit `9f492dc9b725f69c0c85f83fca7abe4cad05c6e7` (sealed appendix A6.17) and commit `0bc65c2ef1edf35cc7ecbce6b2ae3ff3e3743ebf` (review of the fix, below), tree `b7bb8e0c99bdab7abd6a6a9a3ddade7ef1df145a`;
    - they are merged with the private generator stopped;
    - only files outside the content hash changed, so it stays `ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b`.

    The fixes:
    - Keys are lower-case snake_case in task.json, task.md, the truth file and the validity report (`hours_to_trigger_f_1`).
    - `pressure_sensor_fault` has `m_min` and `range` null, with its bounds as text in `bounds`. Its bounds depend on the faulted transmitter's span.
    - `flow_sensor_fault`'s bounds are the same numbers for every flowmeter, so they are now numeric: `m_min` 1 and `range` [-3, 3].
    - F3 tickets state the sign convention and the naming rule.
    - Short lists of resolving options are filled to three with instruments that no tag list logs. Every plant type has at least three such instruments, and a list that stays short raises instead of being written.
    - *Review of the fix.* In the first fix, station lists could stay short: the station tag lists log most of the instruments it added. The review commit adds three kinds of station instrument that no tag list logs: the chamber pressure, the pressure after valve V-1, and a flowmeter in each pump's discharge branch.
    - A new command, `generators rewrite-texts`, rewrites task.md and task.json of the bundles already written. It does this from each bundle's own draw, with its data files checked byte-identical, and logs each rewritten realisation 1 as a new `r1_written`.
  - **The public fix.** `harness/bundles.key_spec` accepts a vocabulary fault whose `m_min` and `range` are both null and whose unit is a string. It is kept with no bounds. Everything else is refused as before: `m_min` null alone, `range` null alone, and bounds given as text.
    - INTERFACE.md now documents key names, null bounds and signed faults.
    - The grader and the headroom rule never read the vocabulary bounds. An identified truth still needs a numeric magnitude.
  - **Tests.**
    - `tests/test_ops_bench_writer_conformance.py` loads, validates and grades `tests/fixtures/opsbench/writer-documents.json`: a task.json and truth.json for each family and F3 stratum, as the private writers write them. A private test keeps the fixture equal to their output.
    - `test_task_json_validation` has new cases for the refused forms.
  - **Scope.**
    - The grader and the 6.6 semantics did not change. No Stage 0 session has run. The pilot's tasks (ops-f1-003, ops-f4-001) are unaffected.
    - `bundles-dev.sha256` and `truth-dev.sha256` are unchanged. They list only ops-f1-003 and the F4 tasks, and the fixed writers reproduce those bundles byte for byte.
    - The F2 and F3 realisation-1 bundles are rewritten before they are committed at freeze-0b.
    - A rewrite of the 16 development bundles on a scratch copy changed only task.md and task.json of the 9 F2 and F3 tasks. The loader then loaded all 16 tasks, and `set_problems` was empty.
    - After the review fix, the rewrite was repeated on a fresh copy. It wrote the same 18 files byte for byte: every current F3 ticket already listed at least three options. The loader again loaded all 16 tasks.
