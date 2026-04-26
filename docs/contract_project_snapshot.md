# Contract — project snapshot (JSON) and views

**Status:** normative for implementation and future changes to this subsystem.  
**Supersedes:** informal requirement to mirror a Google Doc «карточка» layout. Binding to GDoc format is **optional**; the old TZ fragment (HTML copy of the intermediate report) is **non-binding** for storage and rendering decisions.

---

## 1. Purpose

Provide a **single semantic model** of a magister project (phases, facts, links, check outcomes) that:

- accumulates over time;
- is stored in a **machine-friendly, versioned** form (JSON);
- can be **transformed** into:
  - short messages to the student (e.g. Telegram);
  - richer artifacts for admins and the **attestation commission** (e.g. PDF/HTML, print).

All human-facing outputs are **derivatives** of this model, not independent sources of truth.

---

## 2. Definitions

| Term | Meaning |
|------|--------|
| **Project snapshot** | One immutable record of the project state at time *t*: structured data + metadata (`schema_version`, `generated_at`, identity). |
| **Canonical payload** | The JSON object (or equivalent in code) that defines a snapshot’s content. All renderers consume this. |
| **Renderer** | Pure function or module: canonical payload → bytes or string (Telegram text, PDF, HTML, etc.). No business rules that duplicate pipeline logic. |
| **Phase (stage)** | A named step in the official flow (aligned with row-check stages / `fill_status` where applicable). Stored as structured fields, not only as free text. |

---

## 3. Principles (non-negotiable)

1. **Single source of semantic truth per moment**  
   At any release of a snapshot, there is **one** canonical structure. Telegram text, PDF for the commission, and any file on Drive are **views** of that structure (or of a snapshot frozen at generation time).

2. **JSON (or equivalent typed model) as the interchange format**  
   Snapshots are serializable to JSON for storage, logging, and testing. In code, use dataclasses/Pydantic with explicit `schema_version`.

3. **Schema evolution**  
   Every snapshot includes `schema_version` (integer or semver string). Older stored snapshots remain readable: either support migration or preserve read-only legacy handling.

4. **Separation of concerns**  
   - **Construction** of the canonical payload: orchestration from sheet row, enrichment, `RowCheckReport`, config, etc.  
   - **Rendering**: no enrichment or stage logic inside renderers beyond formatting and omitting fields for a given audience.

5. **Audience flags, not duplicate models**  
   Differences between «student phone» and «commission» are **presentation** (length, emphasis, sections) and optional **field filters** on the same payload, not two divergent domain models.

---

## 4. Canonical payload (contractual shape)

Exact field names may evolve with `schema_version`; the following **shape** is required.

### 4.1 Metadata

- `schema_version` — required.  
- `generated_at` — ISO-8601 UTC (or documented local offset if agreed).  
- `row_number` — row in the registration sheet when applicable.  
- `identity` — at minimum: display name (FIO), group; optional stable ids if introduced later.

### 4.2 Registration and links

Structured links and flags as already implied by the product: report URL, project folder, LKB, dissertation, accessibility flags where relevant. Must be able to render **without** re-fetching external documents for a **frozen** snapshot (URLs and last-known labels as stored in the snapshot).

### 4.3 Phases / stages

- Represent **normative** phases (aligned with the checking pipeline: e.g. stages 1–4, `fill_status`).  
- Each phase entry: stable `id`, `status` (e.g. passed / failed / skipped / pending), human `summary`, optional `details`, optional `warnings`, `updated_at` if known.  
- Free-text lines for the student must be **traceable** to structured issues where the pipeline provides them (e.g. from `spravka`-like content), not ad-hoc prose only.

### 4.4 Metrics (dissertation)

Where applicable: pages, sources, compliance — as structured values with explicit «unknown / skipped» states matching pipeline semantics.

### 4.5 Provenance (optional but recommended)

- `trigger`: e.g. `row_check_apply`, `manual_regenerate`, `initial_registration`.  
- `source_fingerprint` or hash of inputs if the pipeline already defines one (for debugging).

---

## 5. Renderers

| Output | Contract |
|--------|----------|
| Telegram / «справка» | Short, plain text or HTML subset; may omit long details; must not contradict the same snapshot. |
| Commission artifact (PDF/HTML) | Full structured sections from the same snapshot; printable, readable in a meeting. |
| Archive file on Drive | Snapshot **file** (JSON and/or derived PDF) named with FIO and timestamp; policy for retention is product decision, not this contract. |

Renderers **must not** re-run Google API calls to «refresh» data unless explicitly implemented as a **new** snapshot build.

---

## 6. When to build a snapshot

Minimum policy (to be implemented explicitly):

- After a **successful** write to the registration sheet from a full row check (when the product should notify the student), **or**  
- On **explicit** admin action («regenerate card / resend»), which creates a new snapshot and may trigger storage + optional broadcast.

Idempotency: generating twice with the same inputs may yield the same canonical payload; storing duplicate files is a product choice (e.g. always new filename with timestamp for audit).

---

## 7. Storage

Acceptable physical locations (choose per deployment):

- Google Sheet cell / helper column (size limits).  
- Separate sheet or tab for snapshot history.  
- Google Drive: `{student_label}_{iso_datetime}.json` and/or PDF alongside.  

The **contract** is the logical snapshot; storage is an implementation detail.

---

## 8. Relationship to the spreadsheet

- The registration sheet remains the **operational** system of record for day-to-day workflow.  
- A snapshot is a **point-in-time export** of interpreted state, suitable for commission and history. If sheet and snapshot disagree, **rebuild snapshot from current pipeline inputs**; do not hand-edit JSON as the default workflow (unless a future admin-edit feature is specified).

---

## 9. Out of scope (unless amended)

- Mandatory use of Google Docs as the storage format.  
- Duplicating business logic inside PDF/HTML template code.  
- A second parallel «card» model maintained only in Telegram strings.

---

## 10. Amendments

- Changes to the canonical field set require a **bump** of `schema_version` and, if needed, a short entry in this document or a changelog file under `docs/`.  
- Breaking renames: migration path or clear deprecation window for stored JSON files.

---

## 11. Implementation checklist (for PRs)

- [ ] Snapshot builder produces one typed object + JSON serialization.  
- [ ] `schema_version` set and tested.  
- [ ] At least one renderer (e.g. Telegram) reads only that object.  
- [ ] Optional second renderer (e.g. PDF) uses the same object.  
- [ ] No new stage/справка text assembled only inside renderers without going through the snapshot builder.

---

*End of contract.*
