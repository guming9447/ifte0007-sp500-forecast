# Forecasting evidence: local publication candidate

This package has NOT been uploaded. Publication requires the student's review.
No raw market observations, per-origin returns, personal details, correspondence,
credentials or formal forecast are bundled. The tests generate invented values.

## Reproduction scope

1. The public package runs synthetic tests without access to a data vendor.
2. On the student's computer, the separately retained frozen raw files reproduce
   the research and historical dry run. These raw files must not be redistributed.
3. A fresh download may not be the same vintage. Exact-download mode rejects a
   different hash, even if only CSV formatting changed. New-version acquisition
   uses a separate directory and does not replace a frozen file.
4. Matching hashes establish file identity, not accurate market values or
   historical publication time. Independent calculations on the same source are
   not independent verification of the market source.

## Runtime and portable tests

Python 3.14.6 on Windows was tested locally. Claude independently ran the exact
v08 package on Linux with Python 3.13.16, no E drive and the student storage
flag unset: all 157 synthetic checks passed. The attached Linux record's code
hashes match this v09 release candidate: only documentation and validation
metadata changed since v08, not executable code or dependencies. See
`metadata/linux_public_check_record.json` and `metadata/release_update.json`.
Core scripts use the standard
library plus IANA timezone data (`tzdata==2026.2` on Windows). The calendar CSV is
bundled metadata, so exchange_calendars is not needed to run these tests.

No E drive is required for public users. Choose a new private output directory
outside this repository on a writable drive. In PowerShell, from this directory:

```powershell
. .\scripts\Set_Private_Runtime.ps1 -PrivateRoot ..\private_forecast
python -B scripts/run_public_checks.py --output ../private_forecast/checks_01
```

The supplied runtime script takes a `-PrivateRoot` parameter; review its help
and creates caches within the chosen private directory. The student's private
launcher enables `BDF_REQUIRE_E_STORAGE=1`; public installations do not set it.
Never unset this flag on the student's computer to write outside the approved
workspace. Public Linux/macOS users can run the same Python check command;
the runner configures its child-process temporary/cache directories itself.
Use the full path to your own
Python executable if `python` does not resolve to the intended interpreter.

Install `requirements.txt` into an existing suitable Python environment first.
For an offline computer, obtain the dependency wheels beforehand with
`python -m pip download -r requirements.txt --dest ../private_forecast/wheels`,
then install using `python -m pip install --no-index --find-links ../private_forecast/wheels -r requirements.txt`.
The dependency wheels are not included. Legacy XLS conversion optionally needs
`requirements_optional.txt`; neither XLS conversion nor a calendar generator is
needed for the bundled synthetic checks or frozen replay.
The separately pinned `requirements_calendar.txt` records the optional calendar
generation environment (including its own tzdata version); do not install it
over the core replay environment. The already-generated calendar and generation
manifest are included so normal replay does not need that toolchain.

The runner executes both implementations' synthetic checks and a fully invented
M1 demonstration, saving commands, input/code hashes and a clearly labelled
synthetic prediction in `public_check_record.json`. It neither downloads prices
nor submits anything. Use a fresh output directory for each run.

## Frozen private research replay

The original data-root directory must contain the relative paths listed in
`metadata/source_registry.json`, including the bundled old calendar at its
original relative path. Run only with personally held, permitted inputs:

```powershell
python -B scripts/reproduce_research.py --data-root ../private_forecast/frozen --output ../private_forecast/replay
python -B scripts/m1_forecast.py --data-root ../private_forecast/frozen --origin 2026-10-09 --output ../private_forecast/historical_demo
```

The replay executes the original v04, A1 and round-2 functions without requiring
private letters or approval documents. It is a replay of historical decisions,
not permission to select a different final model. Its per-origin outputs remain
private. Use this wrapper, not the legacy `backtest_method_v04.py` or v05 main
entry points, which preserve original project-specific gates and layouts.
`test_finalday_tools.py` without `--synthetic-only` additionally needs
the student's saved comparison outputs and original project layout.

Exact downloader example (inspect `--help` for the required registry/id options):
`python -B scripts/download_evidence_inputs.py --help`.
New data acquisition: `python -B scripts/capture_source.py --help`.
No tool runs on a schedule. S&P manual export is required when an automatic
response cannot establish the official dated USD Price Return value.

## Frozen method and source control

M1 is P0 multiplied by exp(mean historical comparable-horizon log price return).
Use up to 260 mature labels, minimum 156; retain the original VIX-eligible sample
membership branch. VIX is not a new slope predictor. 156-week and expanding
estimates are report-only. Four conditional factors failed the recorded screens;
the third proposed research round was not run. M1 is not measured equity risk
premium, since dividends and the risk-free return are absent.

The two agent roles are specification/independent recomputation and local
data/execution/review. The student approves method changes and the final number.
Data -> source and time checks -> immutable lock -> M1 candidate -> independent
raw-source and canonical-input recomputations -> student approval -> submission.
Errors lead to investigation, not automatic substitution or changed thresholds.

The formal pipeline is implemented but requires real-source rehearsal and
human evidence review. An observation date alone is not availability proof.
Final cutoff is 2026-11-13 12:00 London, target 2026-11-20 NY close. Later retrieval
is acceptable only with evidence that the same used version was public before
the cutoff. D-031 option 2, approved 6 November: deployment SP500 inputs from
2016-10-31 through P0 come from S&P DJI USD Price Return. Only the five sessions
2016-10-24 through 2016-10-28 retain frozen FRED values. Original research inputs
and backtests remain unchanged. Fresh FRED SP500 is
optional cross-check only, never a gap filler. Its missing/recently published
rows do not block forecasting. Official missing required sessions stop; a
two-decimal disagreement stops until a dated, version-bound investigation record
confirms official values. VIX history remains FRED with full-precision revision
checks; current VIX is Cboe. A changed official historical value still stops for
student resolution; approval to change source is not approval for revisions.

The private source-control requires source_policy
`D031_OPTION2_OFFICIAL_HISTORY_FIXED_PREFIX`; old controls are rejected.
`sp_official` must cover every session from the fixed boundary through P0.
A rolling download cannot expand the five-day frozen exception. An optional
`sp_crosscheck` is never an estimator input and needs no pre-cutoff input
availability attestation. Preserve its actual capture receipt separately.
`sp_difference_reviews` maps ISO dates to reviewer, basis, resolution
`use_official`, exact official/crosscheck strings and their file SHA-256 values.
Changing either source version requires a fresh investigation record.

No CLI submits or automatically approves a forecast. `calculate` creates a
candidate; `compare` requires exact input hashes, P0, branch, training dates/n,
full-precision difference <=0.005 and identical two-decimal forecasts. A separate
raw-source recomputation and explicit student approval remain necessary.

`independent/` contains unchanged copies of Claude's calculation modules and
three synthetic suites, with hashes in `metadata/independent_code_manifest.json`.
Its `rep_final.py --help` documents its separate CLI. Its review JSON interface
is not interchangeable with the local pipeline's source-control JSON: each
requires exact dates, values, current file hashes, reviewer and basis. Do not
copy an approval between interfaces without translating and checking fields.

## Public-release boundary

This is a technical evidence candidate, not the complete assessment submission.
Under the coursework data-sharing guidance, restricted observations are replaced
with source links, acquisition instructions, hashes and invented examples.
These enable computational inspection; they do not grant data redistribution
rights or guarantee a vendor still serves the historical version. Exact market
result reproduction requires lawful access to the matching frozen inputs.
The report, six-slide presentation, final value, student-confirmed declarations,
sanitised instruction/decision extracts and repository URL must be finalised
separately. Do not upload private source-control files, run folders or original
chat exports. `metadata/package_manifest.json` inventories only this allowlist.
Create a fresh repository from the approved allowlist, without private Git
history. After separately authorised upload, verify access while logged out and
tag the approved submission version. None of those publication actions is
performed by these scripts. `FINAL_FORECAST_PENDING.md` is deliberately pending.
