# Final run: redacted record

All times are UTC (London GMT), 13 November 2026. Source values, return estimates,
personal information and local machine paths are intentionally omitted.

## Acquisition and source checks

- 03:15:39: Cboe official daily file acquired over HTTPS; receipt and original
  preserved privately. S&P automated export returned HTTP403; FRED CSV requests
  timed out. Failures were retained, not reclassified as successful downloads.
- 03:16:50: official S&P page observed with the 12 November date, USD and Price
  Return selected. Page evidence preserved privately.
- By 03:21:40: student-downloaded official S&P XLS and FRED VIX CSV received,
  hashed and copied to private storage. Actual receipt is a conservative latest
  availability bound, not a claim about the first publication instant.
- Official history covers every required session from 31 October 2016. The
  five earlier frozen FRED sessions remain the sole prefix exception.
- Official/frozen price-history overlap and previous-official overlap match;
  VIX frozen-history revision checks and required-date checks passed.
- Newly downloaded FRED SP500 was unavailable. An older cross-check snapshot was
  used only for overlap comparison, never as an input or to fill an official gap.

## Commands and outputs

The following are portable equivalents of the recorded commands. Paths under
`<PRIVATE>` denote separately held, non-distributable files.

```text
python -B scripts/final_day_pipeline.py lock --config <PRIVATE>/source_control.json --output <PRIVATE>/inputs
python -B scripts/final_day_pipeline.py calculate --inputs <PRIVATE>/inputs --output <PRIVATE>/candidate --omit-scenarios
python -B scripts/final_day_pipeline.py calculate --inputs <PRIVATE>/inputs --output <PRIVATE>/offline_replay --omit-scenarios
```

- Locked input manifest SHA-256:
  `387e46bba19d3fc038f64272277377ace247c81ed1ad48db482c75ac1183f4b0`.
- Private candidate SHA-256:
  `0d892cb45ec41f79fed4c7dd1f34fe666f08c177882a201780e0d051087897c6`.
- By 03:24:10: all locked file hashes verified, same-code offline replay exact,
  and arithmetic checked over the selected mature training dates. Same-code
  replay is not described as independent replication.
- The optional scenario omission changes no estimator or source-control rule.
  A synthetic test verifies identical non-scenario fields and that the scenario
  function is not invoked when omission is selected.

## Independent check and human approval

Claude's saved receipt reports input-only staging at 03:54:50, independent XLS
conversion at 03:55:10, two computations at 03:55:11 and a saved receipt at
03:55:28, followed by opening Codex's candidate at 03:55:45. Codex verified the
output/receipt hashes and internally consistent chronology; it did not witness
the cloud workspace's first file opening.

Claude calculated once from the canonical CSV inputs and once from original
sources with its own XLS conversion and calendar implementation. The two modes
agree. Codex subsequently verified 33 checks, including source identity,
canonical input hashes, training dates/hash, branch, sample count and both
report-only window results. Full-precision point-forecast difference is zero;
the mean-log-return difference is approximately 2.17e-19 from floating arithmetic.

The student approved the single final forecast **7,606.95 index points** at
04:14 GMT, as conveyed in the student's approval handoff. The original private
candidate remains marked pending as a historical record; `FINAL_FORECAST.json`
records the later approval without rewriting that candidate.

No raw prices, fitted-return parameters, market series or source screenshots are
included. Code and matching lawfully held frozen inputs reproduce the result;
public synthetic tests alone do not reconstruct the restricted real inputs.
Historical first-release availability remains a disclosed research assumption.

Repository upload, the final tag and Moodle submission require separate actions.
