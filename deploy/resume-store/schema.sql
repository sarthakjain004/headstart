-- Résumé store (ADR-0124). Postgres 16 on the Oracle box, reached over the SSH tunnel the
-- llm-router already uses. Holds a Résumé document only when the Account has switched syncing on
-- for it; a résumé that was never switched on has no row here at all.
--
-- Three tables because the three have different lifetimes and different queries:
--   resume_document   the structure (the Composite tree), the layout id, the theme overrides
--   resume_content    the words — one row per (document, node, variant); 'base' is the master
--   resume_tailoring  one row per tailored version, and the Job it was written for
--
-- The shape is `resume_document.js` at rest. Content is split out rather than kept in the
-- document's own JSON for two reasons that hold: deleting a version must really delete the
-- wordings only it used, which is a cascade rather than application code doing a sweep; and two
-- devices editing different bullets should both land rather than one overwriting the other.

BEGIN;

CREATE TABLE IF NOT EXISTS resume_document (
    -- Client-generated: a document has an identity offline, before it has ever reached this
    -- database, and keeps it when syncing is switched on later.
    id                TEXT PRIMARY KEY,
    -- alerts.store.subscription_id(email) — the same Account identifier the Profile is filed
    -- under. An email address is never stored here.
    account           TEXT        NOT NULL,
    name              TEXT        NOT NULL DEFAULT '',
    layout_id         TEXT        NOT NULL,
    -- The Composite tree: node ids, types, slots, geometry. No words — those are in
    -- resume_content, which is what lets a layout change and a text edit be different writes.
    tree              JSONB       NOT NULL,
    theme             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    active_tailoring  TEXT,
    -- Optimistic concurrency (ADR-0124 §4). Bumped on every accepted write; a push carrying a
    -- stale rev is refused with the server's copy rather than overwriting it.
    rev               BIGINT      NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Soft delete so an accident is recoverable; a scheduled sweep really deletes past the
    -- window the product states. "Deleted" must eventually mean deleted.
    deleted_at        TIMESTAMPTZ
);

-- The listing query: one Account's live résumés, newest first.
CREATE INDEX IF NOT EXISTS resume_document_account_idx
    ON resume_document (account, updated_at DESC)
    WHERE deleted_at IS NULL;

-- The retention sweep's query.
CREATE INDEX IF NOT EXISTS resume_document_deleted_idx
    ON resume_document (deleted_at)
    WHERE deleted_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS resume_content (
    document_id TEXT        NOT NULL REFERENCES resume_document (id) ON DELETE CASCADE,
    node_id     TEXT        NOT NULL,
    -- 'base' is the master's wording. Any other value is a variant a Tailoring forked; it holds
    -- only the fields that DIFFER from the base, so a fix to the base still reaches every version
    -- that never disagreed with it. That is the whole point of the model.
    variant_id  TEXT        NOT NULL DEFAULT 'base',
    fields      JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, node_id, variant_id)
);

-- "Which versions override this block?" — the query behind showing that a master edit will not
-- reach three of your applications.
CREATE INDEX IF NOT EXISTS resume_content_node_idx
    ON resume_content (document_id, node_id);

CREATE TABLE IF NOT EXISTS resume_tailoring (
    id          TEXT        PRIMARY KEY,
    document_id TEXT        NOT NULL REFERENCES resume_document (id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    -- The Job this version was written for, when it came from a search result. Deliberately NOT a
    -- foreign key: Jobs live in the LanceDB search index this database knows nothing about, and
    -- they are evicted (ADR-0023/0083) while the record of having applied must outlive the
    -- posting. Indexed, nullable, never joined across stores.
    job_id      TEXT,
    -- node_id -> variant_id, for the blocks this version rewords.
    picks       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- node ids this version leaves out. The blocks stay in the résumé; they are pruned from this
    -- version when it is resolved.
    hidden      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS resume_tailoring_document_idx
    ON resume_tailoring (document_id);

-- "What did I send to this job?"
CREATE INDEX IF NOT EXISTS resume_tailoring_job_idx
    ON resume_tailoring (job_id)
    WHERE job_id IS NOT NULL;

COMMIT;
