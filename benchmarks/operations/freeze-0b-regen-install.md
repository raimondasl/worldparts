Regeneration check of ops-f1-003, ops-f4-001, ops-f4-002, ops-f4-003 with content hash `ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b` (2026-09-26 14:06); installed 2026-09-26 14:06.

Bundles (every realisation byte-identical to the committed one):

- ops-f1-003: r1 `2eef78fb13eff0e86ff5294c82150fcc72a5f434f2f01b26dd25e5f7e39129c7`, r2 `daf47b6f5c78c81769db2b28a14c40a55718c290b86c29c342bfeed6d3c6c402`, r3 `6ac67eae5b35873adf413ddde4551e97848fc674aefedbc6389e0827690fd14e`
- ops-f4-001: r1 `1c888dd3eac01c54a785402e6b8ad7f1cf3875026d3783296eaf3497ef14b54e`, r2 `2f055955a6934afc9e3bf48c7d3243bf84a123e4b878c8f51fd78982633eb6c7`, r3 `e5f8f4d506a3989a5f39efec5e4768d8a2db426bade9b6751ddfbb3c5bb5ce94`
- ops-f4-002: r1 `fc03b6bfaa0161a8393176da85b697fc69cd05f32f891452f176ffeeb455b70e`, r2 `2df6b78611471a4f13acdbde583775ef16c999f9797edaf34d61e89dc6aea7e6`, r3 `ab7f1c088543050492eb11da8ab6de8ecb587bbcb9fa262c2aa2b9c8ad7396c7`
- ops-f4-003: r1 `042a323f960f731be1ecc1a3ad4b854dc33f918e24d8d0e30a177fef095fe2a8`, r2 `148dd9c41217c4d0b265dcdba4b7973ae3164bf9eaa028ead028fedd17830f83`, r3 `9a89329fd0094a7087be4a1a415a5b16e87d89a34caa53b6da504d94108276e6`

| File | freeze-0a SHA-256 | Now | Kept as |
|---|---|---|---|
| ops-f1-003.meta.json | `9e343b22b87cbaf4faa95f85f3f16b1fdcbdb7008f98fc755bc163bda52efa6c` | `ed14e55521c44264fd256490b84bf915673ff95b2d93c2306da5bf2f5d2c3299` | dev_superseded/freeze-0a/ops-f1-003.meta.json |
| ops-f1-003.truth.json | `b47bf2fa371eb985dba376d229c936c8644b0e2663dd30145b8e708e5e064ec8` | `6b95dc9303010f60f67f1709c7409b153a2d7d00412fc31c3d6b3cb39ea9d9c8` | dev_superseded/freeze-0a/ops-f1-003.truth.json |
| ops-f4-001.meta.json | `97d224fba4129a278ee1e5aef846064021e257d17c07c20a44c0b073f8db43db` | `91d3cdde6e8882a5c58b182127c2cb275f6f18d33b644faae57a92816f5bff6a` | dev_superseded/freeze-0a/ops-f4-001.meta.json |
| ops-f4-001.truth.json | `fc2100fb18efd729ba4685338b2cb8ba311ef5ff698c193c39f591a989b1a82f` | `2865abc36d2a1c08dc60731dd7d649fbac2345f163bf8af7e6d1ff16d681a402` | dev_superseded/freeze-0a/ops-f4-001.truth.json |
| ops-f4-002.meta.json | `87ed1fd3f0fe26550acff08969582aadc533ed21eabaa2c9dfe831d53028dadc` | `20674ba34640d5a3a9dc6de7d8c7645a2a61c087bb7770cca679e6a8ae52efb3` | dev_superseded/freeze-0a/ops-f4-002.meta.json |
| ops-f4-002.truth.json | `2761b1d831ee73e691bf949f070ae4571d2df86232745c52465f783510db1ba7` | `42aff4b62760144b694b674893b0d51cb01bf1157aa655717bf49b4b80c3910e` | dev_superseded/freeze-0a/ops-f4-002.truth.json |
| ops-f4-003.meta.json | `9fb8d2ca604e426c08597a5f98bae9e694fa53b9452b0e4619c1101ef49a9154` | `56e51a1c599b76778b18bb437d91c77b3ea699fab73b86063937071598873620` | dev_superseded/freeze-0a/ops-f4-003.meta.json |
| ops-f4-003.truth.json | `dd3ab47622f0157e535234b718e2a60c0bd420eed6cd07f4058d611e1776194b` | `39ff386643f38afba06197dbeaa285c80321125875d6babd4b2e0a18cf91d6ac` | dev_superseded/freeze-0a/ops-f4-003.truth.json |

Differences and their causes:

- **ops-f1-003.meta.json** (14 differing fields):
  - `content_hash`: "absent" -> "ed64b3e257623db0". freeze-0b: meta.json records the content hash the truth was produced under (A6.15, A6.16).
  - `content_hash_full`: "absent" -> "ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b". freeze-0b: meta.json records the content hash the truth was produced under.
  - `r1_moved_from`: "absent" -> null. freeze-0b: new meta.json field (A6.15).
  - `report.info.r2_first_three.passes`: "absent" -> {}. freeze-0b: R2 on realisations 1 to 3 (PREREGISTRATION 6.5; A6.15).
  - `report.info.r2_first_three.ran`: "absent" -> []. freeze-0b: R2 on realisations 1 to 3 (PREREGISTRATION 6.5; A6.15).
  - `report.info.r2_first_three.realisations[0]`: "absent" -> 1. freeze-0b: R2 on realisations 1 to 3 (PREREGISTRATION 6.5; A6.15).
  - `report.info.r2_first_three.realisations[1]`: "absent" -> 2. freeze-0b: R2 on realisations 1 to 3 (PREREGISTRATION 6.5; A6.15).
  - `report.info.r2_first_three.realisations[2]`: "absent" -> 3. freeze-0b: R2 on realisations 1 to 3 (PREREGISTRATION 6.5; A6.15).
  - this run's times and paths (volatile): `checkpoint`, `report.timing.asimov_s`, `report.timing.mc_s`, `report.timing.total_s`, `screen_passed`, `seconds`.
- **ops-f1-003.truth.json** (1 differing fields):
  - `r2_applies`: "absent" -> true. freeze-0b: truth.json's new top-level field, whether R2 is a reference of the task (PREREGISTRATION 6.5, INTERFACE.md; sealed appendix A6.15).
- **ops-f4-001.meta.json** (9 differing fields):
  - `content_hash`: "absent" -> "ed64b3e257623db0". freeze-0b: meta.json records the content hash the truth was produced under (A6.15, A6.16).
  - `content_hash_full`: "absent" -> "ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b". freeze-0b: meta.json records the content hash the truth was produced under.
  - `n_max`: "absent" -> 200. written by the freeze-0b generation code; the generator stage of 2026-09-25 did not record it.
  - `n_real`: 200 -> null. freeze-0b meta.json records the sequential rule's stopping count as n_real; an F4 validation has no sequential stop (its Monte Carlo size, 200, is n_max), so n_real is null; the 2026-09-25 generator stage wrote the Monte Carlo size there.
  - `r1_moved_from`: "absent" -> null. freeze-0b: new meta.json field (A6.15).
  - this run's times and paths (volatile): `checkpoint`, `screen.seconds`, `screen_passed`, `seconds`.
- **ops-f4-001.truth.json** (1 differing fields):
  - `r2_applies`: "absent" -> true. freeze-0b: truth.json's new top-level field, whether R2 is a reference of the task (PREREGISTRATION 6.5, INTERFACE.md; sealed appendix A6.15).
- **ops-f4-002.meta.json** (9 differing fields):
  - `content_hash`: "absent" -> "ed64b3e257623db0". freeze-0b: meta.json records the content hash the truth was produced under (A6.15, A6.16).
  - `content_hash_full`: "absent" -> "ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b". freeze-0b: meta.json records the content hash the truth was produced under.
  - `n_max`: "absent" -> 200. written by the freeze-0b generation code; the generator stage of 2026-09-25 did not record it.
  - `n_real`: 200 -> null. freeze-0b meta.json records the sequential rule's stopping count as n_real; an F4 validation has no sequential stop (its Monte Carlo size, 200, is n_max), so n_real is null; the 2026-09-25 generator stage wrote the Monte Carlo size there.
  - `r1_moved_from`: "absent" -> null. freeze-0b: new meta.json field (A6.15).
  - this run's times and paths (volatile): `checkpoint`, `screen.seconds`, `screen_passed`, `seconds`.
- **ops-f4-002.truth.json** (1 differing fields):
  - `r2_applies`: "absent" -> true. freeze-0b: truth.json's new top-level field, whether R2 is a reference of the task (PREREGISTRATION 6.5, INTERFACE.md; sealed appendix A6.15).
- **ops-f4-003.meta.json** (9 differing fields):
  - `content_hash`: "absent" -> "ed64b3e257623db0". freeze-0b: meta.json records the content hash the truth was produced under (A6.15, A6.16).
  - `content_hash_full`: "absent" -> "ed64b3e257623db020ef374f1b776b04a464b7fe2d406f0dbd2bbca90246bf8b". freeze-0b: meta.json records the content hash the truth was produced under.
  - `n_max`: "absent" -> 200. written by the freeze-0b generation code; the generator stage of 2026-09-25 did not record it.
  - `n_real`: 200 -> null. freeze-0b meta.json records the sequential rule's stopping count as n_real; an F4 validation has no sequential stop (its Monte Carlo size, 200, is n_max), so n_real is null; the 2026-09-25 generator stage wrote the Monte Carlo size there.
  - `r1_moved_from`: "absent" -> null. freeze-0b: new meta.json field (A6.15).
  - this run's times and paths (volatile): `checkpoint`, `screen.seconds`, `screen_passed`, `seconds`.
- **ops-f4-003.truth.json** (1 differing fields):
  - `r2_applies`: "absent" -> true. freeze-0b: truth.json's new top-level field, whether R2 is a reference of the task (PREREGISTRATION 6.5, INTERFACE.md; sealed appendix A6.15).
