# Frozen method and reproducibility boundary

## Forecast

The deployed model is M1: F = P0 * exp(mu). P0 is the S&P 500 USD Price Return
close on the last session before the Friday 07:00 America/New_York origin.
The target is the last session on or before the following Friday. Early closes
and holidays use the bundled timezone-aware session calendar.

For each past Friday, r = log(target close / baseline close). Only labels whose
actual target close precedes the current cutoff may enter estimation. mu is the
arithmetic mean of up to 260 most recent eligible labels, with at least 156.
This is a comparable-horizon price drift, not a dividend-inclusive return or an
estimated equity risk premium.

The frozen sample-membership branch is retained: use labels with the VRP proxy
available when the current proxy exists, at least 156 such labels exist and
their proxy values vary. Otherwise use mature labelled observations. The proxy
is VIX squared minus annualised realised variance over 22 daily log returns,
expressed in percentage-squared units. It changes eligibility only; M1 does not
estimate or deploy a VIX coefficient. See m1_forecast.prepare/estimate.

Forecast calculations retain full precision. Submission uses decimal half-up
rounding to two places. Historical scores use the unrounded forecast:
max(0, 50 - 500 * abs(F - actual) / actual), rounded to two places. Window
sensitivity (156 or expanding) and hypothetical outcomes are report-only.

## Research history, not renewed selection

Research tested overnight futures F1, a VRP proxy F2, 20-session momentum F3,
and the VIX/VIX3M term-structure proxy F4_TS. Sequential rounds and the A1
intermediate-volume amendment are disclosed. The later 52 weeks were already
seen and are not an untouched holdout. Operational screening does not control
multiple-testing risk. All four failed the recorded admission rules; M1 remains
final. A third round was withdrawn before calculation. Frozen legacy functions
are included for replay, not permission to redesign or reselect models.

## Sources, authority and evidence

Original research uses exact-hash frozen snapshots. Deployment follows the
fixed official-history boundary in AGENT_INSTRUCTIONS.md and README.md.
An observation date or matching hash is not evidence of pre-cutoff publication.
For 2026-11-13 12:00 London, the version used must have been publicly available
by that time. Later acquisition needs evidence of that same version's earlier
availability. Source conflicts stop for investigation, not silent substitution.

Independent comparison checks input hashes, P0, branch, sample count, ordered
training-date hash, unrounded forecast within 0.005, and identical rounded
forecasts. The date hash is SHA-256 of ascending ISO dates joined with commas,
no spaces, UTF-8. The student, not either program, approves the final number.

## What the public package establishes

Synthetic examples and tests are fully runnable offline once dependencies are
installed. Actual historical result replay additionally requires lawful access
to the hash-matching private inputs. No actual prices or per-origin returns are
published. Successful tests are not proof of vendor accuracy or historical
availability. Final-day operations and the actual rehearsal remain separate
from these computational checks.
