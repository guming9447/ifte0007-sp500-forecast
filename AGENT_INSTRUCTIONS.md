# Sanitised agent instructions and change history

This is a summary of the student's approved constraints, not a verbatim chat
transcript. Identifying details and private correspondence are intentionally
omitted. Original dated instructions remain in the student's private archive.

## Authority and division of work

- The student owns the research object, approves specifications and amendments,
  and makes the final submission decision. Neither agent may approve itself.
- Local agent: retrieve sources, preserve originals, implement, test and review.
- Independent agent: propose specifications, implement separately before reading
  per-origin results, compare results, draft the report and slides.
- Independence of code does not establish independence or accuracy of data.
- Preserve disagreement, correction, decision, code and input-version records.

## Research controls

- Define factors, expected signs, lookbacks, availability and admission rules
  before computing the affected performance. Record all attempts and failures.
- A1 amended only intermediate futures-bar volume eligibility; endpoint volume,
  missing-hour and gap checks remained. It followed the zero-valid-window finding,
  not a favourable F1 score. Original and amended results remain preserved.
- Round 2 followed round 1 and was disclosed as sequential research. The later
  52-week period was already seen, not a clean independent holdout. Screening is
  operational and does not control multiple-testing risk.
- A third round was contemplated but withdrawn before data collection/calculation.
- M1 was retained and method/re-estimation rules were frozen on 2 November 2026.
- F1 source investigation was closed by disclosure, not by proving true volume
  or contract identity. New material evidence must still be assessed.

## Final operational constraints (3 November, updated 6 November)

- D-031/D-032 option 2: final deployment uses S&P DJI official USD Price Return
  daily history from 2016-10-31 through P0. Only 2016-10-24 through 2016-10-28
  retain frozen FRED. Do not move that boundary when a rolling export shortens.
  New FRED SP500 is cross-check only, never a missing-price substitute; delayed
  FRED rows do not block a complete official series. Research snapshots remain
  unchanged. VIX history remains FRED; P0 VIX is Cboe.
- Any raw-precision difference between frozen historical values and official
  history stops. Cross-source two-decimal conflicts require a review bound to
  series, date, values and both current file hashes, naming reviewer and basis.
  A stale or generic review cannot authorise a new conflict.
- Rehearsal and formal execution begin only when the student messages. No
  autonomous scheduled source monitoring, forecasts or publication are enabled.
- The E-only storage restriction belongs to the student's private runtime, not
  to the public research method. Public scripts accept user-chosen paths;
  the student launcher retains the opt-in E-drive and cache guard.

- P0: dated S&P DJI USD Price Return close. Manual export if automated retrieval
  fails. Current VIX: Cboe official close. FRED/Yahoo are cross-checks, not silent
  substitutes for an unavailable official P0.
- A two-decimal source disagreement is investigated. Once field/date are verified,
  official current data prevail; never average conflicting providers.
- If current official VIX is unavailable, document it and use the already frozen
  labelled-sample fallback. Do not insert zero or forward-fill a missing value.
- Historical revisions or missing expected sessions require investigation and
  student approval of the resolution. Keep frozen history, additions and hashes.
- Use only versions public before the 13 November 12:00 London cutoff. Actual
  retrieval may occur later only when same-version pre-cutoff availability is
  evidenced. Dates and hashes alone do not prove publication time.
- Independent checks require the same inputs, P0, branch, training membership and
  count; full forecasts differ by at most 0.005 and rounded values match exactly.
- Sources are checked only on request. The scheduled monitor was cancelled;
  missed times are not backfilled with invented captures.
- Rehearsal: student starts it on 10 November, simulating 6 November 07:00 New York,
  using 5 November P0, targeting 13 November close. It is not the final prediction.
- Private raw data and per-origin market outputs are not uploaded, including to a
  private repository. Public package contents require separate student approval.
