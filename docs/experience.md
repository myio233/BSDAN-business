# Experience Notes

## What we missed

- We treated "one happy path passed" as if the whole feature was correct.
- We validated multiplayer mostly from the host page and did not verify the guest path end to end.
- We did not assert that `human_count + bot_count` exactly matched the report standings row count.
- We did not verify that report rank and totals matched the stored model output for the actual room member.
- We did not verify report download as a real browser download event.
- We did not verify home-city changes from the room page all the way through round context and report context.
- We missed a partial-home-city bug: the selected city changed the visible loan limit, but multiplayer settlement still fell back to Shanghai for interest, material, and storage costs.
- We missed a business-rule bug in average salary smoothing: one single-player branch skipped the configured smoothing rule entirely.
- We over-trusted local process state and local scripts instead of checking the deployed domain under nginx.

## Why Playwright missed obvious bugs

- Some checks were implementation-shaped, not user-shaped.
  The script looked for DOM markers and page loads, but did not assert the business invariants that users actually care about.
- We did not turn explicit user formulas into direct assertions.
  That let the code drift from the agreed salary rule while the browser flow still looked superficially correct.
- The validators were brittle.
  At least one script depended on stale selectors, so the check could fail for the wrong reason or stop before reaching the real bug.
- The domain session model was different from local test assumptions.
  Writing users into the local auth store file does not automatically update the already-running deployed process, so "account created locally" was not the same as "domain login works now".
- We accepted cached or prewarmed states as proof.
  A warmed report image cache is not proof that clicking the download control produces a real PNG file for the current user session.
- We did not inspect artifacts aggressively enough.
  Screenshots and room/report payloads were generated, but we did not compare them against expected participant counts, expected selected city, or per-user submission state.

## New screening rules

- Always run at least one full review against the deployed domain behind nginx.
- Always include both host and guest users for multiplayer checks.
- Always verify these multiplayer invariants:
  - selected home city survives from lobby to round page
  - selected home city changes all city-dependent finance inputs, not only the displayed loan limit
  - round-1 workers and engineers start at zero
  - standings row count equals `humans + bots`
  - KPI totals and rank match the stored report model output
  - each human can submit and then reach the report flow
- Always verify these salary invariants:
  - current average salary is the weighted average of active workers or engineers
  - published next average salary equals the currently configured smoothing formula
- Always verify report download with a browser download event and a saved PNG file.
- Always inspect screenshots after the run instead of trusting only HTTP status codes.
- Treat "room page looks loaded" and "report page rendered" as necessary but not sufficient.

## Operational checklist

- Use real user credentials already known to the deployed service, or restart the service after creating new file-backed accounts.
- Save screenshots for setup, room, round 1, report, and final summary.
- Save a machine-readable summary of row counts, ranks, totals, selected city, and download result.
- If a run fails, capture the exact page URL, body excerpt, and recent server logs before making code changes.
