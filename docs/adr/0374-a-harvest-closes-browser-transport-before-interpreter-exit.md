# ADR-0374: Harvest closes browser transport before interpreter exit

**Status:** accepted · **Date:** 2026-10-02 · **Relates to:** ADR-0056

Python disables thread-executor submissions before ordinary `atexit` callbacks. Pydoll's async
browser exit may reconnect to CDP through executor-backed DNS, so deferring that exit to `atexit`
caused every browser-using shard in the audited window to fail cleanup. Subprocess tests reproduce
the exact exception, while real Chrome tests verify both process and profile removal.

The shared harvest closes browser transport in its `finally` path while the interpreter is still
usable. Shutdown cancels outstanding transport calls, closes Chrome and its event loop, and
preserves the original harvest result or exception. Each harvest binds its workers to a browser
lifetime when their wrapper is constructed; a dispatched worker entering after cancellation
cannot adopt the next lifetime and restart Chrome. New harvests and direct callers may reopen it.

Direct callers retain an `atexit` fallback that synchronously reaps the Chrome process and then
its profile, without asynchronous networking. Keeping async exit in `atexit` with more retries
cannot restore an executor Python has already shut down. Permanently disabling the transport on
an interrupted harvest was rejected because a later harvest in the same process must still work.
