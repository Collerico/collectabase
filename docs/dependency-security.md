# Dependency vulnerability remediation

## Scope and baseline

This update addresses the 30 open Dependabot alerts collected from GitHub:
20 npm advisories in `frontend/package-lock.json` and 10 Python advisories in
`backend/requirements.txt`. Severity breakdown: 14 high, 12 medium, 4 low.
Several advisories affect the same installed package; counts are advisories,
not distinct packages. Runtime and development dependencies are both in scope.

This change does not suppress audit checks or dismiss alerts. Existing CodeQL
alert 1 (administrator key in browser localStorage) is unrelated and unchanged.
No database migration, authentication redesign or feature change is intended.

## Advisory inventory

The patched-version column is the advisory's minimum fixed version, not a claim
that every minimum is mutually compatible. The selected dependency set must
satisfy all applicable advisories and framework dependency constraints.

| GitHub alert | Severity | Package | Advisory | Vulnerable range | First patched |
|---|---|---|---|---|---|
| [#1](https://github.com/Collerico/collectabase/security/dependabot/1) | high | `python-multipart` | `GHSA-2jv5-9r88-3w3p` | `<= 0.0.6` | `0.0.7` |
| [#2](https://github.com/Collerico/collectabase/security/dependabot/2) | high | `python-multipart` | `GHSA-59g5-xgcq-4qw3` | `< 0.0.18` | `0.0.18` |
| [#3](https://github.com/Collerico/collectabase/security/dependabot/3) | high | `python-multipart` | `GHSA-wp53-j4wj-2cfg` | `< 0.0.22` | `0.0.22` |
| [#4](https://github.com/Collerico/collectabase/security/dependabot/4) | medium | `python-multipart` | `GHSA-mj87-hwqh-73pj` | `< 0.0.26` | `0.0.26` |
| [#5](https://github.com/Collerico/collectabase/security/dependabot/5) | medium | `python-dotenv` | `GHSA-mf9w-mj56-hr94` | `< 1.2.2` | `1.2.2` |
| [#6](https://github.com/Collerico/collectabase/security/dependabot/6) | high | `python-multipart` | `GHSA-pp6c-gr5w-3c5g` | `< 0.0.27` | `0.0.27` |
| [#7](https://github.com/Collerico/collectabase/security/dependabot/7) | low | `python-multipart` | `GHSA-vffw-93wf-4j4q` | `< 0.0.30` | `0.0.30` |
| [#8](https://github.com/Collerico/collectabase/security/dependabot/8) | low | `python-multipart` | `GHSA-6jv3-5f52-599m` | `< 0.0.30` | `0.0.30` |
| [#9](https://github.com/Collerico/collectabase/security/dependabot/9) | low | `python-multipart` | `GHSA-v9pg-7xvm-68hf` | `< 0.0.31` | `0.0.31` |
| [#10](https://github.com/Collerico/collectabase/security/dependabot/10) | high | `python-multipart` | `GHSA-5rvq-cxj2-64vf` | `< 0.0.30` | `0.0.30` |
| [#11](https://github.com/Collerico/collectabase/security/dependabot/11) | medium | `esbuild` | `GHSA-67mh-4wv8-2f99` | `<= 0.24.2` | `0.25.0` |
| [#12](https://github.com/Collerico/collectabase/security/dependabot/12) | high | `rollup` | `GHSA-mw96-cpmx-2vgc` | `>= 4.0.0, < 4.59.0` | `4.59.0` |
| [#14](https://github.com/Collerico/collectabase/security/dependabot/14) | medium | `picomatch` | `GHSA-3v7f-55p6-f55p` | `>= 4.0.0, < 4.0.4` | `4.0.4` |
| [#15](https://github.com/Collerico/collectabase/security/dependabot/15) | medium | `vite` | `GHSA-4w7w-66w2-5vf9` | `<= 6.4.1` | `6.4.2` |
| [#16](https://github.com/Collerico/collectabase/security/dependabot/16) | medium | `postcss` | `GHSA-qx2v-qp2m-jg93` | `< 8.5.10` | `8.5.10` |
| [#17](https://github.com/Collerico/collectabase/security/dependabot/17) | medium | `vite` | `GHSA-fx2h-pf6j-xcff` | `<= 6.4.2` | `6.4.3` |
| [#18](https://github.com/Collerico/collectabase/security/dependabot/18) | medium | `vite` | `GHSA-v6wh-96g9-6wx3` | `<= 6.4.2` | `6.4.3` |
| [#19](https://github.com/Collerico/collectabase/security/dependabot/19) | high | `postcss` | `GHSA-6g55-p6wh-862q` | `<= 8.5.11` | `8.5.12` |
| [#20](https://github.com/Collerico/collectabase/security/dependabot/20) | high | `postcss` | `GHSA-r28c-9q8g-f849` | `<= 8.5.17` | `8.5.18` |
| [#21](https://github.com/Collerico/collectabase/security/dependabot/21) | medium | `postcss` | `GHSA-fxqj-rqcc-2cmp` | `<= 8.5.22` | `8.5.23` |
| [#22](https://github.com/Collerico/collectabase/security/dependabot/22) | high | `nanoid` | `GHSA-28wg-ghj8-5hjv` | `< 3.3.16` | `3.3.16` |
| [#23](https://github.com/Collerico/collectabase/security/dependabot/23) | high | `nanoid` | `GHSA-2v37-7h3g-55p8` | `< 3.3.18` | `3.3.18` |
| [#24](https://github.com/Collerico/collectabase/security/dependabot/24) | high | `nanoid` | `GHSA-xwg4-73v4-xw9w` | `< 3.3.12` | `3.3.12` |
| [#25](https://github.com/Collerico/collectabase/security/dependabot/25) | high | `@vue/server-renderer` | `GHSA-g2v6-rqmx-r4w6` | `< 3.5.42` | `3.5.42` |
| [#26](https://github.com/Collerico/collectabase/security/dependabot/26) | high | `source-map-js` | `GHSA-68fv-2mgg-jv7q` | `>= 1.0.0, < 1.2.2` | `1.2.2` |
| [#27](https://github.com/Collerico/collectabase/security/dependabot/27) | medium | `vite` | `GHSA-698v-4r3x-3vcv` | `<= 6.4.3` | `6.4.4` |
| [#28](https://github.com/Collerico/collectabase/security/dependabot/28) | high | `vite` | `GHSA-587h-7wmj-h5pm` | `<= 6.4.3` | `6.4.4` |
| [#29](https://github.com/Collerico/collectabase/security/dependabot/29) | medium | `vite` | `GHSA-r825-w8mm-5pxw` | `<= 6.4.3` | `6.4.4` |
| [#30](https://github.com/Collerico/collectabase/security/dependabot/30) | medium | `vite` | `GHSA-rq7h-c2jc-7f22` | `<= 6.4.3` | `6.4.4` |
| [#31](https://github.com/Collerico/collectabase/security/dependabot/31) | low | `vite` | `GHSA-9jrq-w75r-8gcw` | `<= 6.4.3` | `6.4.4` |

## Compatibility and verification

- Keep Python 3.11 and the existing Node 20 build/runtime contract.
- Choose patched, mutually compatible releases rather than forcing latest major
  versions or using overrides to hide incompatible dependency requirements.
- Refresh npm's actual lockfile with the package manager and validate a clean
  `npm ci`, production build, Vue typecheck and frontend tests.
- Resolve Python dependencies into a clean environment and run the API/import,
  backup, pricing, static-file and security regression suites. Audit the resolved
  runtime dependency set independently of the auditing tool's environment.
- Add dependency security-floor regression tests to both CI test commands.
- Run npm audit and pip-audit without advisory ignore lists. Local audit success
  must be followed by GitHub checks and a post-merge readback of the original
  Dependabot alerts; scanner database/graph refresh can be asynchronous.

## Selected versions and compatibility rationale

| Dependency | Previous resolved/pinned version | Patched selected version |
|---|---|---|
| Python multipart parser | 0.0.6 | 0.0.31 |
| python-dotenv | 1.0.0 | 1.2.2 |
| FastAPI | 0.109.0 | 0.133.1 |
| Starlette (existing transitive runtime, now explicitly pinned) | 0.35.1 | 1.3.1 |
| Vite | 5.4.21 | 6.4.6 |
| Vue / server-renderer | 3.5.28 | 3.5.46 |
| esbuild | 0.21.5 | 0.25.12 |
| Rollup | 4.57.1 | 4.68.9 |
| picomatch | 4.0.3 | 4.0.7 |
| PostCSS | 8.5.6 | 8.5.34 |
| nanoid | 3.3.11 | 3.3.20 |
| source-map-js | 1.2.1 | 1.2.4 |

The independent Python audit exposed vulnerable FastAPI and transitively resolved
Starlette versions beyond the two packages named by the original Dependabot
requirements alerts. Starlette 1.3.1 covers the audited parser/static security
fixes; FastAPI 0.133.1 permits Starlette 1.x, while 0.132.1 requires `<1.0.0`.
The explicit Starlette pin prevents the resolver from selecting an older
vulnerable release. HTTPX and the other backend pins remain unchanged.

Vite 6 is used instead of the audit tool's suggested latest major upgrade, keeping
the existing Node 20 contract. The manifest requires Vite `^6.4.4`, Vue `^3.5.42`
and the compatible Vue plugin `^5.2.4`; the lockfile records the concrete versions
above. npm resolves transitive patches normally; no dependency overrides are used.

The patched form parser deliberately rejects excessive URL-encoded field counts
and oversized multipart text fields. Ordinary uploads, including files larger
than the disk-spooling threshold, retain their metadata/content and are covered
by compatibility tests. No application route or business-logic code is changed.

## Verification results

- Freshly resolved Python runtime: 37 packages, no dependency conflicts and zero
  known vulnerabilities from pip-audit (auditor installed separately).
- npm audit: zero known vulnerabilities, including development dependencies.
- Full backend discovery: 100 tests pass, including seven added dependency/parser
  regressions and the optional image-optimizer test.
- Explicit backend CI sequence: 99 tests pass (the optional optimizer is outside
  that existing sequence).
- Frontend: clean npm install, production build, Vue typecheck, and 12 Node tests
  pass, including ten added dependency-tree security-floor tests.
- Security version-floor tests do not replace vulnerability-database audits.
- Python 3.11 and Node 20 remain unchanged; container build validation is performed
  by the existing GitHub publishing workflow rather than a local Docker daemon.
