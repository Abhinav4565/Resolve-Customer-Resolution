# Validation

Checked on 14 October 2026. [Machine-readable test results](test-results.json) include the command, actual output and source-file hashes.

The completed suite passed **13 tests**, with no failures or errors. Coverage includes exact refund entitlement after prior refunds, approval gating, stale approval rejection, changes checked inside the write transaction, an actual post-commit worker exit and reference reconciliation, duplicate or corrupted refunds, missing replacement dispatch, unavailable authoritative sources and conflicting customer evidence. Three retry checks cover explicit mode selection after provider failure, fresh source inspection, live-mode retry, and refusal to replan saved business effects.

## Recorded evidence

The [137.97-second narrated video](../submission/demo.mp4) contains two labelled chapters. A live Claude proposal is followed by exact approval, a real worker interruption, a new PID, one replacement and a separate source check. A different reference run then demonstrates stale approval, the exact excess-payment refund, accepted investigation handoff and rejection of a corrupted refund.

The edit uses synthetic narration and frame holds. The two chapters show separate runs; they are not a generalization benchmark. Explicit model retry and the expandable model explanation were added after recording.

Desktop and mobile inspection found no navigation errors, JavaScript errors or horizontal overflow in the checked views. The cover is illustrative; application screenshots and source captures show the actual interface.

Run `python -m unittest discover -s tests -v`. These results do not establish live payment-provider reliability, real carrier integration or arbitrary complaint understanding. The checker proves the configured replacement-dispatch or refund policy outcome, not final replacement delivery.

An isolated browser smoke check also created a provider-failure fixture, explicitly selected reference mode for retry, and verified version advancement without resetting other cases. The unverified model explanation remains escaped and expandable across refreshes. No live model quality is implied by those fixtures.
