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
- **2026-09-26, Stage 0 first pass (commitments and run log).** It ran from the frozen worktree at `9cb3564`, with the owner's approval per batch of 8, in `benchmarks/operations/results/stage0`.
  - **Sessions.** All 64 ran (16 tasks x 2 arms x 2 models), one at a time. The final blind audit shows 0 flagged and 0 to re-run. The API-equivalent cost is about $27.
  - **Infrastructure re-run.** At about 21:22 every `python.exe` on the machine ended: the harness, the private generator, and the owner's unrelated agent4evr processes, which a watchdog restarted at 21:32. The cause is outside this project.
    - One session (`facc2cfa29392b8b`: ops-f3-005, `code+`, Sonnet 5) was flagged `harness_interrupted`, and it was re-run under 6.6 as attempt 2.
    - Its orphaned CLI process ran on until 21:30. For those minutes it overlapped the first batch-8 session (`eb2417a1fb07776f`, ops-f4-001: a different task).
    - That session never named `/tmp` or `TEMP` in a tool input. The only content of the shared session `/tmp` was the CLI's own `claude` folder, kept in both attempts' records.
    - The seven batch-7 sessions that had not started ran afterwards.
  - **ops-f1-004 rejected.** Validation rejected attempt 0 (`reference_pass`) after its four Stage 0 sessions had run. Those sessions are superseded. Attempt 1 was rejected too (`reference_pass`) before any session. Both bundles are in the private `dev-rejected/`.
  - **ops-f1-004 replacement.** Attempt 2's realisation 1 is committed here (r1 digest `c7f8e0a8c76b558aa4ff204f108c61c278721be53841f7cf1e60bee8c48d1a90`). Its sessions run in a new run directory once its validation has decided, so they are not spent on a draw that is rejected again. This orders the work; it does not depend on any answer or grade.
  - **ops-f1-002.** Its truth (`c7409236d41966f00786a860ce9abd8af7ea7ee94938974c2178b01a68a9d93b`, content hash `ed64b3e257623db0`) and its realisations 2 and 3 are committed here. Validation accepted attempt 1 with its realisation 1 unmoved.

- **2026-09-26 23:10, validation.** ops-f2-001 was accepted (attempt 1, realisation 1 unmoved): its truth `36e7c1a248c4ff3bcfa60eefc133c4fa571bac18ae0162126507f12d5c6ce888` (content hash `ed64b3e257623db0`) and realisations 2 and 3 are committed. ops-f1-004 attempt 2 was rejected (`reference_pass`, before any session); attempt 3's realisation 1 is committed (r1 digest `be7a6666b9ce359ce8fde3f6cb8dc5694905b2e582e007a90664e9331051a671`); its sessions wait for its validation.
- **2026-09-26 23:41, validation.** ops-f2-003 was accepted (attempt 0, realisation 1 unmoved): its truth `5d5d15e484f76f8bf8b2a4754a11c11304cd9c6cfb9634bd97572111a4b1565b` (content hash `ed64b3e257623db0`) and realisations 2 and 3 are committed.
- **2026-09-27 02:43, validation.** Accepted, each with realisation 1 unmoved; truths (content hash `ed64b3e257623db0`) and realisations 2 and 3 committed: ops-f1-004 attempt 3 (`1c6fea23fabeb4d2e9cd3fc79de16d0f910733296c7553d1f0a9348fbd446a3a`; its r1 was committed at 23:10, its replacement sessions have not run yet), ops-f2-002 attempt 1 (`aa6e934f7bf775bb4dc554dd086f56b9a09e84a4a304d500a6bfc3097c9274a9`), ops-f3-001 attempt 1 (`edb6651eb7fb8864af98f2bf12a24201c457f25149be959ddbab34e779255c29`).
- **2026-09-27 10:20, validation.** Accepted, each with realisation 1 unmoved; truths (content hash `ed64b3e257623db0`) and realisations 2 and 3 committed: ops-f3-002 (`1833111f2d09e2bda0fe8d294f4485ccac076f187a75a0f2d1a3fd89b1cd2047`), ops-f3-003 (`1f7427988b036c7e66f95311b71f00d414c9ede1ee4c5c257093b77aca6a6c06`), ops-f3-004 (`5adb2fb4d869a868ff52081756edbfc25e0a41ad52febdac804892c59efb2f5f`), ops-f3-006 (`3759cf88cb3eebc2e3e0bb2f9ca512e0196a2f64b3523f56332e0665595cfd03`). ops-f3-005 (F3/G-epa, no_fault): attempt 2, on which its four Stage 0 sessions ran, was rejected (`naive_fail`), so those sessions are superseded; attempts 3 to 60 were rejected too (screen `asimov`, `reference_pass`, `naive_fail`, `transmitter_range`, `error`), a cell acceptance far below 10 % to be redesigned before freeze-1 (6.5); attempt 61's realisation 1 is committed (`ff4ea5c547aa2a70ea1370a86c9098906328b195fe06e7a0ddbce7fba1959070`); the cell's redraw limit of 100 draws applies. The room became OPEN at 02:43 under the bound form (F-(Sonnet 5) = 4).
- **2026-09-27 10:40, Stage 0 replacement run.** ops-f1-004's four sessions ran on attempt 3's committed realisation 1 in `benchmarks/operations/results/stage0-replace-f1-004` (worktree `d0f43fc`, same settings; 0 flagged; $4.10). The four earlier sessions of ops-f1-004 in `stage0` are superseded. The rule is computed over both run directories. With 15 of 16 slots decided (ops-f3-005 still being redrawn), F-(Sonnet 5) = 6 and F-(Opus 5.5) = 2: the room is OPEN.
- **2026-09-27, build-phase tooling (new tooling for the build phase below and the freeze-1 arms, written by the orchestrating session before any builder session).** It adds tools; it changes no Stage 0 rule, grade or setting.
  - **New files.**
    - `harness/score.py` and the command `score DIR`: scripted answers `DIR/<task_id>/r<k>.json`, each graded as a final reply by `grading.grade_answer`, on every realisation of the tasks listed in `truth-dev.sha256`. The full mode (owner only) writes `DIR/score.json` and prints the realisation pass rate. The pass/fail mode (`--pass-fail --session NAME`) prints PASS or FAIL per task realisation and the number of passes, logs each evaluation in `DIR/pass-fail.log` and refuses a fourth evaluation for the same session name.
    - `tools/ops_passfail.py DIR --session NAME`: the builders' wrapper of the pass/fail mode. It reads the truth folder from `WPBENCH_OPS_TRUTH` itself.
    - `harness/firewall.py` and the command `firewall-audit`: scans builder transcripts for forbidden reads (the paths of the protocol below, the truth folder, `git` history of PREREGISTRATION.md, and searches or wildcard reads over a run directory that would read `record.json` without naming it) and counts pass/fail evaluations per session, with line numbers and a verdict.
    - `harness/quickref.py`: the test of section 3 on `quick-reference.txt`, fixed now, before track A writes it: at most 45 shown lines and 20 for section 14 (marked by `#! section 14` ... `#! end`, never shown to the agent); every number one of the checklist's (1 and 90); every prose word a word of the checklist or of a fixed list of reference words; a line with a procedure word only when it is verbatim in the checklist; no section-14 name used as code outside its part. A change to these rules is logged here.
    - `harness/toolkit.py`: the `code-skill` toolkit from `WPBENCH_OPS_TOOLKIT`, copied into each session as `reference/`, recorded by its SHA-256 manifest, and refused when its text would trip a contamination marker of the code arms.
    - `preambles/environment-lib.txt` (the text of `environment.txt` with worldparts added to the package list), `preambles/lib-directed.txt` (the directive of section 3, verbatim) and `preambles/code-skill.txt` (the sentence, verbatim). They are frozen at freeze-1 with the quick reference.
    - `tests/test_ops_bench_build.py`, on synthetic bundles, transcripts and toolkits, with the fake CLI.
  - **Readiness arms.** `run --set dev --readiness` lets `code-skill`, `lib-directed` and `lib` run for the readiness checks of section 3 (default arms `lib-directed` and `code-skill`, default model `claude-sonnet-5`). Without the flag they stay unavailable until freeze-1, and `mcp-hybrid` stays unavailable. The lib environment is the code-plus packages at the code-plus environment's versions plus a worldparts wheel built from the repository; its key hashes the commit and the wheel's source SHA-256, and `run.json` records the wheel's SHA-256 and the commit (`lib_env`, `lib_key`). A run refuses a wheel built from sources that differ from the commit, and a wheel whose files would trip a contamination marker of the lib arms. Licence files are left out of that check: the Apache licence text has "APPENDIX" in capitals, the frozen marker of the sealed appendix, so a session that opens an installed licence (of worldparts, or of numpy or matplotlib, whose licence files have it too, in every arm) is flagged and reviewed by hand; the marker is not changed. `code-skill` sessions keep the markers of the code arms, so any use of worldparts contaminates them.
  - **Frozen files changed**, none in what a Stage 0 session gets or how it is graded:
    - `harness/__main__.py`: the commands `score`, `firewall-audit` and `lib-env`; `run --readiness`, `--toolkit` and `--lib-env`; each session gets its arm's Python environment; `run.json` gains `readiness`, `lib_env`, `lib_key`, `toolkit` and `toolkit_digest`, and a resumed run refuses another value of them.
    - `harness/arms.py`: the readiness flag of an arm, the preamble check, and `build_prompt` and `preamble` taking `readiness`.
    - `harness/env.py`: the lib environment; `python_env_for("lib")` only with `readiness`.
    - `harness/runner.py`: the working-directory extras (code-skill's `reference/`, checked against the run's digest, recorded in `extras.json` of the attempt).
    - `harness/__init__.py`, the README and INTERFACE.md ("Scripted answers", "Build-phase inputs"): documentation.
    - `tests/fixtures/opsbench/fake_claude.py`: it tells the new arms apart by their preambles.
    - `tests/test_ops_bench_harness.py`: two whole runs with the fake CLI use at most 2 fake sessions at a time instead of 8 and 4, for the courtesy mode of the machine. No assertion changed.
  - **Unchanged:** the grader (`grading.py`), the headroom rule, the infrastructure rules, the contamination markers (`markers.py`), `checklist.txt`, `environment.txt`, `stage0-settings.json` and the settings `headroom` checks. The `code+` and `code-hint` prompts, command lines and environments are byte-identical, and the existing tests pass with their assertions unchanged.
- **2026-09-27, review fixes of the build-phase tooling (before any builder session).** A review of the entry above found that the pass/fail mode could be used as an oracle of the truth, that the evaluation limit and the audit could be bypassed, and gaps in the quick-reference test, the toolkit check and the readiness pins. Each finding was reproduced on synthetic fixtures and fixed with tests; no builder session had run.
  - **Pass/fail mode** (`harness/score.py`, `harness/__main__.py`, `tools/ops_passfail.py`):
    - **The oracle.** A bundle copy whose `task.json` lost a fault or an option (through `WPBENCH_OPS_BUNDLES`) made the truth check fail exactly when that was the truth, and the failure logged nothing. Now the bundles of the scored tasks must match the committed `bundles-dev.sha256` before any truth file is opened. `--truth`, `--truth-manifest`, `--bundles`, `--out` and `--no-manifest-check` are refused.
    - **The variables.** The truth folder comes from `WPBENCH_OPS_PASSFAIL_TRUTH`, which `truth_root()` never reads. So the owner's commands (full scores, `grade`, `headroom`) do not run in a builder session, which no longer gets `WPBENCH_OPS_TRUTH`.
    - **Counting.** The session's name comes from `WPBENCH_OPS_SESSION` (a different `--session` is refused and logged). The log is the owner's (`WPBENCH_OPS_PASSFAIL_LOG`, by default `%LOCALAPPDATA%/worldparts-bench/ops-build/pass-fail.log`), never in the builder's `DIR`, so deleting `DIR` or picking a new name does not reset the count. Each evaluation is counted under a lock file and logged before anything is graded, so two runs at once cannot both be the third, and a failed or crashed run counts. The log is parsed so that a line without its line break cannot hide the next evaluation.
    - **Errors.** An error after that point gives one message that names no truth. A run that would score nothing (a wrong truth folder) is refused before it is logged.
  - **Scoring** (`harness/score.py`):
    - An answer file the grader cannot grade fails with a `parse_error` instead of stopping the run: an integer too large for a float, or an array nested deeper than Python recurses.
    - The full mode writes every grade to `--out FILE` outside `DIR` (not `DIR/score.json`) and checks the scored bundles against the committed manifest (`--no-manifest-check` to skip).
    - It prints the pass rate with one decimal, rounded down (43/48 was printed "90 %"), and a line `readiness ...: MET` or `NOT MET` with the passes needed (the smallest integer at or above 90 % of N, computed exactly). `readiness_met` and `readiness_needed` are added to the document.
  - **Firewall audit** (`harness/firewall.py`). New violations:
    - `score.json`, `WPBENCH_OPS_PASSFAIL_*` and the owner's `%LOCALAPPDATA%/worldparts-bench`. With this entry, `score.json` joins the files builders never read, beside `record.json`, `summary.*`, `headroom*.json` and `pass-fail.log`.
    - Wildcard spellings of the forbidden names (`*opsbench*`, `tru*`), and `cd` into `reports`, `truth`, `research_notes` or `.claude`.
    - A recursive search, copy or archive (`grep -r`, `find -exec`, `xargs`, `cp -r`, `robocopy`, `tar`, `zip -r`, `Copy-Item -Recurse`, ...) over a folder above a run directory (`.`, the repository, `benchmarks`, a checkout such as the Stage 0 worktree), unless it is restricted to transcript and code files. Over a folder above the repository (`..`, world-model, the home folder, a drive) it is a violation even when restricted, and so are `rg`, the `Grep` tool and multi-level `Glob` patterns there. `rg`, the Grep tool and `git grep` elsewhere stay allowed, since they skip the git-ignored run folders.
    - A wildcard read of a run directory by any command but a lister (`jq`, `grep` without `-r`, `python` glob), and paths such as `sessions/*/*.json`.
    - The owner's commands (`harness score` without `--pass-fail`, `grade` without it, `headroom`, `report`), the pass/fail mode with the owner's options, and the harness's truth functions called from Python. Setting `WPBENCH_OPS_SESSION` is a violation too.
    - A change to the harness, its committed lists, the wrapper or the benchmark's tests, by a write tool or a shell write. The quick reference, which track A writes, is excepted.
    - Other sessions' transcripts: session-transcript tools, `claude --resume` or `--continue`, the terminal reader.
    - `git log -p`, `show`, `diff`, `checkout`, ... of a folder that holds PREREGISTRATION.md (`.`, `benchmarks`, `benchmarks/operations`), and fetches of its history from GitHub (a commit URL, `commits?path=`, `raw.githubusercontent.com/<commit>`), by shell or `WebFetch`, whose URL is now scanned.
    - Text written by a write tool: a script (`.py`, `.sh`, `.ps1`, ..., or no suffix) is scanned like a shell command. Other written text, such as notes that quote these rules, gives warnings only; the review had asked for violations there too.
    - Evaluations are counted for every launcher (`uv run -m`, `py`, `$PY`, `./tools/ops_passfail.py`, `runpy`, the harness's `main`).
  - **Firewall audit, new warnings:**
    - an evaluation inside a loop, `xargs`, a background job or a written script;
    - `git diff` of an old revision, and `git checkout`, `switch`, `worktree add` or `reset --hard` to one;
    - setting `WPBENCH_OPS_BUNDLES`.

    Setting `WPBENCH_OPS_BUNDLES` stays a warning, and the pass/fail mode still honours the variable (the review had asked to ignore it): the manifest check makes the bundle folder's location irrelevant.
  - **Quick-reference test** (`harness/quickref.py`, a change to its rules, logged as its docstring requires; the quick reference does not exist yet):
    - `.95` and `1_000` are numbers.
    - These are read as prose:
      - string literals;
      - backtick spans of three or more hyphen-joined words;
      - names of more than four underscore-joined words;
      - prose tokens joined by `/`, `,` or `;`.
    - Words with letters that are not ASCII are refused.
    - A shown line has at most 120 characters.
    - A line with a procedure word is refused even when it copies the checklist. Section 3 says the quick reference has "no thresholds and no procedure", and the `lib` arm gets it without the checklist. Before this change, the test's example passed the checklist line "Prefer none, then single faults".
    - The section-14 names are every public name `worldparts/__init__.py` imports from the section-14 modules, so a name track A adds (a SCADA reader) counts at once. The modules are `measurements`, `calibration`, `diagnosis`, and `scada` or `identifiability` if they exist.
  - **Toolkit** (`harness/toolkit.py`): refused when a file is not UTF-8 text (a pickle or an archive would escape both checks). Refused, too, when it has more lines than section 3's cap: 800, or the line count of the section-14 modules (5,364 lines today), whichever is larger.
  - **Readiness pins** (`harness/__main__.py`, `harness/env.py`):
    - **Preambles.** `run.json` records the SHA-256 of each preamble file its arms use (`preambles`). A resumed run and a re-run refuse other texts. A readiness run refuses preamble files that differ from the commit. The re-run path checks the preambles first.
    - **The lib key.** It hashes the last commit that changed the wheel's sources, not HEAD. So a commit such as this file's entries no longer blocks resuming or re-running a readiness run. HEAD at build time is recorded in the environment's stamp.
    - **No rebuild in place.** A resumed run or a re-run uses the run's lib environment directory and never rebuilds it in place. An explicitly given `--lib-env` that holds another environment is refused, not rebuilt.
    - **Ignored files.** The dirty check includes git-ignored files that the wheel build packs (an example `.inp`); caches are excluded.
  - **Grader issue, logged and not fixed.** `grading.as_number` calls `float()` on any JSON integer, so an integer above about 1e308 raises `OverflowError`. This crashes `grade RUN` on such a final reply; no Stage 0 reply has one. Scoring now catches it (above). A fix in `grading.py` waits for the defect rule of 6.6, and `grading.py` is unchanged.
  - **Documentation and tests.** The README, INTERFACE.md ("Scripted answers", "Build-phase inputs") and `tests/test_ops_bench_build.py` are updated; the new tests use synthetic fixtures and the fake CLI only. No other test changed.
  - **Unchanged:** the grader, the headroom rule, the infrastructure rules, the markers, the checklist, the `code+` and `code-hint` preambles, prompts, command lines and environments, `stage0-settings.json` and the Stage 0 settings.

## Build phase after Stage 0 (protocol fixed before any builder session, 2026-09-27)

The room is open. The owner chose a staged path: first the build and the readiness checks of section 7, then a go/no-go on the test-set phase. The readiness checks are the dev-set `lib-directed` and `code-skill` runs of section 3 and the scripted pipeline and time limits of section 7. This protocol applies the firewall of section 7 and is fixed here before any builder session.

- **Tracks.**
  - **A** finishes worldparts: only the additions allowed after freeze-0a, a scripted worldparts pipeline with no LLM for the readiness check, and the quick reference.
  - **B** writes the `code-skill` toolkit, in a workspace with no worldparts source. Its session has not seen worldparts' section-14 code.
  - The orchestrating session writes neither.
- **Inputs of every builder session:**
  - the public repository, except the draft history of PREREGISTRATION.md (no `git log -p` or `git show` of it);
  - the development bundles in `opsbench-bundles/dev`;
  - the Stage 0 transcripts, meaning each attempt's `prompt.txt`, `stream.jsonl` and `workdir/`, but never `record.json`, `summary.*`, `headroom*.json` or `pass-fail.log`;
  - the checklist;
  - at most 3 pass/fail evaluations per session, through a wrapper that logs each call.
- **Forbidden paths:** the private folder, the truth folder, `reports/`, `research_notes/`, and the Claude memory folder.
- **Audit.** Every builder transcript is scanned for forbidden reads and for its evaluation count. The results are logged here.
- **Equal budgets.** At most 3 builder sessions per track, and at most 8 agent-hours of wall-clock time per track. The toolkit is capped at the larger of 800 lines and worldparts' section-14 line count at freeze-1.
- **Readiness (section 7, unchanged):**
  - the scripted worldparts pipeline passes at least 90 % of development realisations;
  - `calibrate` and `diagnose` finish within 300 s on every development bundle;
  - `lib-directed` and `code-skill` each run once on the development set with Sonnet 5, and both pass rates are committed.
- **Owner's go/no-go (an investment decision, not a rule of the pre-registration).** The test-set phase starts only if three things hold on the development set:
  - Sonnet 5 `lib-directed` is near Opus 5.5 `code-hint` from Stage 0;
  - it is clearly above Sonnet 5 `code-hint`;
  - it is clearly above `code-skill`.

  Otherwise the owner closes or pivots.

## freeze-0c, 2026-09-27 (before the Stage 0 verdict is reported)

**Private folder.** It is unchanged since freeze-0b:

- **Commit** `0bc65c2ef1edf35cc7ecbce6b2ae3ff3e3743ebf` (tree `b7bb8e0c99bdab7abd6a6a9a3ddade7ef1df145a`).
- **Archive hash** `71bac2b44b42074c57d7fdee994fa083793a576163cd9e112dfc2f6f7ef67df1`.
- **Content hash** `ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b`, equal to freeze-0b's.
- **Logged fixes after freeze-0b:** none.

**Truths and bundles.** This commit holds every truth file the verdict used: 15 validated tasks in [truth-dev.sha256](truth-dev.sha256), each produced under content hash `ed64b3e257623db0`. It also holds the final development bundles in [bundles-dev.sha256](bundles-dev.sha256).

**ops-f3-005 is unfilled.** Its cell, F3/G-epa, used all 100 draws of its seed sequence (section 8, redraw limit), and the last draw's bundle was moved out. The slot stays undecided.

**Verdict.** It was computed by the frozen harness (worktree at `3074737`, whose grading and headroom code is identical to freeze-0b's) with `headroom --final` over `stage0` and `stage0-replace-f1-004`:

- F(Sonnet 5) = 6 and F(Opus 5.5) = 2, over 15 decided tasks, with u = 1.
- **The room is OPEN.** The early bound verdict of 2026-09-27 02:43 is unchanged.
- The four superseded sessions of ops-f1-004 were not scored.
- The final blind audit shows no pending re-run.
- No session was contaminated.

**Cost.** API-equivalent, every attempt included:

- pilot: $2.17;
- Stage 0 main run: $27.29;
- ops-f1-004 replacement run: $4.10.

**What is not published yet.** Per-task grades are not published until the gate decision, because the build-phase firewall allows builders only pass/fail. They are kept in the owner's private report.

## Build phase paused (2026-09-27)

- **Paused before any builder session.** No builder session has run. Before one could start, the owner asked for a full account of what the build would cost and bring, and paused the phase. The protocol above stays as fixed.
- **Options under review.** The owner is reviewing, with the Stage 0 results in a private report:
  - deciding now;
  - a free check of the transcripts;
  - a small exploratory probe of Sonnet 5 `lib-directed` with the current worldparts on the development set;
  - the full staged build.

  Any choice other than the staged build is logged here before it runs.
- **Partial disclosure of per-task results, already public.** The 10:20 entry above gives F-(Sonnet 5) = 4 while the decided tasks were known, which narrows which tasks Sonnet 5's `code-hint` failed. Any future builder session is told this at its start.

## Owner's decision and logged fixes after the Stage 0 verdict (2026-09-27)

**The owner's decision.** Of the options under review (entry above), the owner chose the free check of Stage 0 transcripts first. It read the transcripts of two development tasks, ops-f3-002 and ops-f4-001, and traced their results to defects of the private generator, not to the agents' methods. The owner then chose to settle the gate without a build phase:

- The build phase above will not run, and no builder session has run.
- The benchmark is fixed first (below), and the Stage 0 sessions whose bundles change run again.
- The owner decides on the worldparts library after the fixed results.

The exploratory probe was not run.

**Logged fixes** of the private generator (PREREGISTRATION 7, "Changes after the first Stage 0 session"; sealed appendix A6.18). Two read-only audits then searched the whole generator for more defects of the same two kinds: an estimator reading a hidden value as known, and a ticket that states the question differently from how the truth is computed. The fixes:

- **D1 (ops-f3-002).** The treatment-train generator ran every train's pump at 48 Hz. A train on a drive logs that command, but a direct-on-line train does not: its plant.md states a 50 Hz motor on the grid, while its pump ran at 48 Hz. The estimators' operating log took the frequency from the simulation, so they knew the hidden value; an agent could not.
  - *Fix.* A direct-on-line train runs at its motor's nominal frequency, and plant.md says the pump runs direct on line at the grid frequency. Trains on a drive are unchanged.
  - *Scope.* ops-f3-002 is the only treatment train of the development set.
- **D2 (ops-f4-001).** An F4 ticket listed the pump's as-built curve as a nuisance (flow scale 0.92 to 1.08, head scale 0.95 to 1.05, ...). When plant.md prints the pump's works-test certificate, validation takes the pump as built from it, so the ticket and the truth described different problems.
  - *Fix.* A certified pump's ticket states no as-built nuisance.
  - *Scope.* ops-f4-001 is the only development task with a certificate. Its truth does not change; its task.md does.
- **D3 (no development task).** Under valve flow control, a treatment train's valve position reached every estimator but no agent.
  - *Fix.* The flow controller's output is logged as an exact tag, FC-001, like a drive's frequency command.
  - *Scope.* The only development train is under fixed control.
- **Found and not fixed.** The audits also found wording mismatches that change no graded development answer, measured on the draws. Fixing them would change bundles whose answers stand and re-run their sessions for nothing. They are listed in A6.18, which is published with the private folder.

**Private folder after the fixes.**

- **Commit** `72068c7eaf21a76dec428845af13a01f1bebd607` (tree `c5288fdebe05f9cec235d3aafc7f1697edf24fb0`).
  - It includes `6d51442`: the fixes, A6.18, their tests, and a `supersede` command that sets an accepted task's files aside so that the generator validates it again.
  - `72068c7` shortens a help text.
- **Archive hash** `ad709044ec4ab16c6664b700fda02c359fbaf64da3ce37d1b73a1c788f641967`.
- **Content hash** `396e1b34b697a57e93911ebfd8425c8ca305ae3c050b819da38d733ee3d3dadc`, over the 60 files in [fix-2a-private-content.sha256](fix-2a-private-content.sha256). Four covered files changed from freeze-0c's `ed64b3e257623db0`: `opsim/design.py` (D1), and `opsim/sensors.py`, `generators/train.py` and `validation/models/base.py` (D3).
- **Tests.** The private fast suite passes: 377 tests, 3 skipped. Two of the skips are the tests of ops-f3-002, whose truth is set aside until it is validated again.

**Every development task validated again under the new code (section 7).**

- **Checkpoints.** 44 checkpointed rows were recomputed bit for bit under the new code, all identical: realisation 1 of the oracle, R-a, R-b and naive on the 11 accepted station and filtration tasks (F1 to F3). The 116 checkpoints of the old hash were then migrated to `396e1b34b697a57e`. ops-f3-002's was not: it was set aside first.
- **Regeneration check** of the 14 accepted tasks other than ops-f3-002, from the migrated checkpoints; the F4 tasks were drawn and validated again: all 42 bundles (r1 to r3 of each) are byte-identical, and every truth file is identical (0 differing fields). Each validity report differs only in the content hash it records and in its times. The reports are kept as they are: each still names the code its truth was produced under, and this entry records that the new code reproduces it.
- **ops-f4-001.** `rewrite-texts` rewrote the task.md of r1 to r3 from its draw; every other file is byte-identical. New realisation digests:
  - r1 `84ac7ba62e4a8ef0d2cf70195a5a1d5f2b61846dad8947e8801e2d6553871241`;
  - r2 `ea9e5e5b490cf82812726cfa0c977bafa6c65f0f07c7ecd173018f2785427537`;
  - r3 `b42133de861463f71e0f2eda748b220a50de9533abe9b30c9cadb6dac13f3da9`.

  Its truth is unchanged.
- **ops-f3-002.** Attempt 0 was superseded: its r1 `02ac89cf077e3bbd9bc6d40f38a6c79bc5756dfa284c944cf4fdd44a17d9909e`, its truth and its checkpoint are set aside, with the reason in its attempt log. The generator validates attempt 0 again under the new code. Its new realisation 1 and truth are recorded here when validation decides, and its sessions run only then, as for ops-f1-004.

**Sessions that run again (section 7).** Only those whose bundle digest changed: the four sessions of ops-f4-001 and the four of ops-f3-002.

- They run in a new run directory, `benchmarks/operations/results/stage0-fix-2a`, with Stage 0's settings (`stage0-settings.json`), one at a time.
- Every session is graded again, and the rule is computed over all three run directories. The verdict from before the change is reported beside the new one: OPEN, F(Sonnet 5) = 6 and F(Opus 5.5) = 2.
- **The harness for the re-run** is the one at this commit. It differs from freeze-0b's only by the build-phase tooling (`dc9e5e5`, `2610f55`), whose entries above record that the `code+` and `code-hint` prompts, command lines and environments are byte-identical. The re-run's prompts and command lines are compared with Stage 0's.

- **2026-09-27 21:16, validation of ops-f3-002 after D1.** Attempt 0 was validated again under content hash `396e1b34b697a57e` and accepted at n = 75, with realisation 1 unmoved. New realisation digests:
  - r1 `5d4ebd4f1a0eb4148a95c2aa43d02c0961824ec43d2797a2ac8ecc6d853970db`;
  - r2 `acb0ab37a43fa6439b57ef252081437b787e6a35c78200e7970563f6db295cc4`;
  - r3 `85ea33a898c8f3a3b253a8a664619c9b8ed8e92583f1694ae6115fd4bd14d542`.

  Its truth file is byte-identical to the superseded one (`1833111f2d09e2bda0fe8d294f4485ccac076f187a75a0f2d1a3fd89b1cd2047`): the draw's fault and magnitude are the same, and D1 changed only how the pump runs and what plant.md says. This commit lists the bundles and the truth. The task's four sessions run in `stage0-fix-2a` after it.
- **2026-09-27, `stage0-fix-2a`: ops-f4-001's four sessions.** They ran from the worktree at `d1b8543` with Stage 0's settings, one at a time. Their prompts differ from Stage 0's only by D2's line. Their command lines differ only by temporary paths and the spelling of the pinned CLI's path.
  - The Sonnet 5 `code+` session reached the 2,400 s limit: a failure (6.6).
  - The Sonnet 5 `code-hint` session's attempt 1 was cut at about 19:28, when every `python.exe` on the machine ended, the owner's own resident processes included.
    - This is the second such event, after 2026-09-26 21:22. Its cause is outside the project and not yet known: a crash, low memory, Defender and every script found on the machine were ruled out.
    - A read-only process watcher now logs what starts before the next one.
    - The blind audit flagged the attempt `harness_interrupted`, and it ran again as attempt 2 (6.6). No orphaned CLI process was left.
  - The two Opus 5.5 sessions ran without flags. The final audit shows nothing to re-run.
  - **The first validation process of ops-f3-002** ended at 18:49 because of the orchestrating session's own tooling: it had been started from a shell whose processes end with it. It resumed from its checkpoints, and no result depends on it.

## Stage 0 verdict after the logged fixes (2026-09-27)

**What was computed.** The frozen harness (worktree at `8ae9949`; grading and headroom code identical to freeze-0b's) graded every session again. It then computed `headroom --final` over `stage0`, `stage0-replace-f1-004` and `stage0-fix-2a`.

- **Sessions scored.** The rule counts only sessions on current bundles. The 12 sessions on bundles replaced since they ran are not scored: the four earlier sessions each of ops-f1-004, ops-f3-002 and ops-f4-001.
- **Re-run sessions.** ops-f3-002's four sessions ran in `stage0-fix-2a` after its validation (entry above), with no flags. The final blind audit of the run shows nothing to re-run, and no session was contaminated.

| | Before the fixes (freeze-0c) | After the fixes (binding) |
|---|---|---|
| F(Sonnet 5) | 6 | **4** |
| F(Opus 5.5) | 2 | **0** |
| Undecided slots u | 1 (ops-f3-005) | 1 (ops-f3-005) |
| Verdict | OPEN | **OPEN** |

**Why it is still OPEN.** F(Sonnet 5) = 4 meets the rule's threshold (F ≥ 4) exactly, so the verdict is the same. Both fixed tasks now pass in `code-hint` for both models.

**Cost of `stage0-fix-2a`.** It is API-equivalent. The sessions reported $5.41. Two had no result message, so their cost is estimated from their token use at the other Sonnet 5 sessions' rate: about $2.3 for the session that reached the time limit and about $2.0 for the attempt cut by the machine-wide stop. So the run cost about $9.8, and Stage 0 about $43 in all.

**Not published yet.** Per-task grades are still in the owner's private report. How and where the results are published is the owner's next decision; so is what the verdict means for the worldparts library.

## Follow-up study H1 locked before its first run (2026-09-29)

H1 is a private pre-registered follow-up. It asks whether automated validity auditors find the generator defects that the Stage 0 gate missed (the logged fixes above). Its criteria, runner, item builder and sealed item key were fixed before any auditor session. The manifest `FREEZE-H1-v1.sha256` lists:

```
a661db51d52277f3a0d34f48f8f03af77818078c2780ae67e54b1ee495288e42  PREREG-H1-draft.md
621bab752c0b46c6d4db17317ab1639ad35e5e252bf174c8f55545250aa4ec94  run_auditor.py
d70add2729c821d7cd9df314c897e719e826e2188e221da1cca72f9e0aec016b  build_items.py
9c2b8f7abd8dc86d504d35618a349c52e53ff5812c3e87a17b1d4332c304f21f  item-key.json
```

The manifest's own SHA-256 is `c893eed9d001b7fc0690a66d0eab249f41130421eec5ecb702c8c135e0e8fcf2`.

- **One planned change.** The item key gains one entry when the last item's draw is accepted. Its new hash will be logged here before that item runs.
- **Publication.** The files are published with the study's results.

## H1: labels and settings amendments locked before any verdict was read (2026-09-29)

Before any auditor verdict was opened, these three private manifests were locked. Each lists the SHA-256 of its files.

| Manifest | What it locks | SHA-256 |
|---|---|---|
| `FREEZE-H1-labels.sha256` | the witness step's labels of the defect candidates, and the witness reports | `24df254bd64b029b92df4907b27e6e03e12e3e634e69430380dc2370ca20e9da` |
| `FREEZE-H1-v1a.sha256` | amendment 1: the settings of the two non-Claude auditor arms | `eaa66f9c5fb2f29d11ef14440ec267fef1497f65092d7d5bdd837308133ab2b1` |
| `FREEZE-H1-v1b.sha256` | amendment 2: those arms were re-run after an environment fault on our side | `433594fdb429f0bd90d45ac8552a3753521bbcfdcabd32d4c4848221a119de12` |

These are published with the study's results.

## H1: last item added, and later records (2026-09-29)

The contest's last item was generated with the pre-fix code (content hash `ed64b3e257623db0`) and accepted by the pre-fix gate. Its hashes are logged here before any auditor saw it.

| What | SHA-256 |
|---|---|
| `item-key.json` with the last item added | `0a03d78c926112a66361739374fcb4647b5f0e747dfe5eae64a582863e1fe10c` |
| The item's bundle files (`d3-bundle.sha256`) | `c66b604398bf0d1de855b152af0959bcdf893f0d8db2bc315008124d88f432c3` |
| Amendment 3: an exploratory run of the auditor without the generator source, locked before it ran (`FREEZE-H1-v1c.sha256`) | `5f7bb8437c84dc0d66cf396b0dc23188c5810e537e34be71f8e826654d326f8f` |
| Amendment 4: one non-Claude arm excluded after it left its task folder (`FREEZE-H1-v1d.sha256`) | `c4b91033b4382efe34de7b6afb5912639b2969ce97341d75e2059e27f7dce5a0` |
| A single-input ablation of the gate, locked before it ran (`FREEZE-H1-mech.sha256`) | `2f872a358b25cc133f9c58f6a0215d06292af837e9bb6dc9a436e251067eaf0d` |
| The ablation's result (`MECH-H1-result.md`) | `b79f7ea8a2746482e75edd0d9fc4deeea30c0f806a557a9f14cc0368bed63a87` |
| Adjudication of an auditor's flags on clean items (`FREEZE-H1-adjudication.sha256`) | `59fd372eec817de5690b7af377462b09b1000e54d0e2e49b541bf41994e65721` |

All of these are published with the study's results.

## Follow-up study H2 locked before its first stage-1 run (2026-09-30)

H2 is a private pre-registered follow-up to H1. It tests automated validity auditors at scale, on tasks from public benchmarks that already carry published third-party labels. Its criteria, sample draw, sealed item key, task files, prompt, containment, runner and scoring were fixed before any stage-1 session. The manifest `FREEZE-H2.sha256` has 726 entries:
- the pre-registration, with the owner's decisions and every deviation found while building the pilot;
- the harness scripts;
- the sample and the sealed item key;
- each task's files;
- each task's source image digest and the ID of its sanitised image;
- each task's offline environment check.

The manifest's own SHA-256 is `9be4c2fa105c6c4b578ac735a58af8c63def9a773e961312f0ec58b008c2a475`.

- **Staged design.** Stage 1 runs first. Stage 2 runs only if stage 1 passes the pre-registered futility rule.
- **Later arms.** Arms added later get their settings locked by an amendment, logged here before they run.
- **Publication.** The files are published with the study's results, after the benchmarks' maintainers have been contacted.

## H2: stage-1 result locked before any adjudication (2026-09-30)

Stage 1 of H2 ran under the manifest above. The pre-registered interim decision is recorded: the binding futility rule was not met, so the confirmatory hypothesis is declared not supported and the confirmatory study stops. Before any adjudication or exploratory follow-up, the private manifest `FREEZE-H2-stage1.sha256` (1,209 entries) locks:
- the interim report and the run log;
- the scorer's output and the void list;
- the leak-review records;
- the raw output of every session: outcome, verdict, full transcript, proxy log, scan and prompt.

The manifest's own SHA-256 is `b7336e57dee1a03863f9a5b5586447746ad25cf051824fd5f1f09eaf39663987`.

The files are published with the study's results, after the benchmarks' maintainers have been contacted.

## H2: amendment 1 locked before its first session (2026-10-01)

After the stage-1 stop, the owner asked for two exploratory follow-ups. Neither changes the stage-1 result or any frozen file.
- **A second auditor model** on the 40 stage-1 tasks, with the settings that the frozen runner already holds.
- **An adjudication of 20 tasks:** 12 where the auditor and the published labels disagree, plus 8 controls where they agree. The tasks are under neutral IDs, and the judges are blind to labels and audit verdicts. The procedure has three steps:
  1. mechanical evidence: fixes written from the issue alone, and a search for a patch that resolves the issue yet fails the tests;
  2. a non-Claude adjudicator;
  3. the owner, on every case where those two disagree.

The private manifest `FREEZE-H2-A1.sha256` (35 entries) holds:
- the amendment and its scripts;
- the session prompts;
- the decision rule;
- the sealed task key;
- the unchanged frozen harness files it relies on.

Its SHA-256 is `d99875967ec5f62ce61669e0079252a1df537c048dfbd092983d01a50d7ebfda`.

## H1 and H2: results locked (2026-10-01)

Three private manifests lock results that the owner's exploratory follow-ups produced. Each lists the SHA-256 of its files.

| Manifest | What it locks | Entries | SHA-256 |
|---|---|---|---|
| `FREEZE-H1-results.sha256` | H1's results report and the three auditor arms' scores. Unchanged since the results were written; re-checked today | 4 | `f6a9f33b694b8f4519a95c84124cb4d650fbf2e300a815268b328aea95bc8b05` |
| `FREEZE-H1-rederive.sha256` | The re-derivation of H1's two disputed labels by a non-Claude model: its protocol (committed before the first session), prompts, runner, results, each session's full output and working folder, and the operator's reproduction log | 530 | `14f3ce86a5b8a7e37b53e78cdafbc436cdf7d5c9e7e490d7150d2595936055e2` |
| `FREEZE-H2-A1-results.sha256` | H2 amendment 1's results (the second auditor model and the adjudication of 20 tasks): the reports, decisions, analyses and leak reviews, and every session's raw output | 1,456 | `3f2cf3c906b4802a7f88a8f2ef3d1a6355b1c7665a09bfedad938fbf6ae1caab` |

These are published with the studies' results, after the benchmarks' maintainers have been contacted.

## Corrections to earlier entries (2026-10-01)

A number audit of our records on 2026-10-01 found these errors in the entries above. The entries stay as written; this entry corrects them.

1. **The machine-wide stops during Stage 0 were caused inside the project** (entry "stage0-fix-2a", commit `8ae9949`).
   - **What happened.** Two events ended every `python.exe` on the machine: 2026-09-26 21:22 EDT and 2026-09-27 about 19:28 EDT. Both were issued by Sonnet 5 sessions under test, not by anything outside the project.
     - Session `facc2cfa29392b8b` (attempt 1, `code+`) ran `taskkill //F //IM python.exe` at 2026-09-27T01:22:08Z.
     - Session `d6a0a018e96801dd` (attempt 1, `code-hint`) ran `taskkill //F //IM python.exe //T` at 2026-09-27T23:28:24Z. That command also ended the harness process running the session.
   - **Why it could happen.** The sessions ran natively on Windows, and nothing prevented a session from ending host processes. The later studies run agents in containers.
2. **The scoring of `d6a0a018e96801dd` attempt 1 is unchanged.**
   - That attempt was flagged `harness_interrupted` and re-run, and attempt 2 passed.
   - On 2026-10-01 the owner ruled that the re-run stands.
   - The Stage 0 verdict is unchanged: F(Sonnet 5) = 4, F(Opus 5.5) = 0, room OPEN.
3. **The 18:49 end of the first ops-f3-002 validation process** (same entry) was caused by the Sonnet 5 `code+` session on ops-f4-001 (`eb2417a1fb07776f`), not by the orchestrating session's tooling. That session ran `taskkill //F //PID … //T` on the process tree at 2026-09-27T22:49:51Z (18:49:51 EDT). No result depends on it, as stated.
4. **The H1 v1 entry** (commit `7dd71a8`). The four lines printed there are a listing of the manifest. The file itself writes each line as `<hash> *<name>`, and the SHA-256 given (`c893eed9…`) is that of the file, not of the printed listing.
5. **The results entry** (commit `64309a2`).
   - "The re-derivation of H1's two disputed labels" should read "the re-derivation of H1's D1 and D2 labels by a non-Claude model: D1 was confirmed, and D2 became disputed".
   - `FREEZE-H1-results.sha256` locks the results of the pre-registered contest. Only the other two manifests come from exploratory follow-ups.
