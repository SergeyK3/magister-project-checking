Disaster Recovery And Operational Safety Strategy

Confirmed Current Architecture

This strategy preserves the current stable architecture:





Telegram bot state is stored with PicklePersistence in [magister_checking/bot/app.py](d:\MyActivity\MyInfoBusiness\MyPythonApps\12%20MagisterProjectChecking\magister_checking\bot\app.py). It persists user_data, chat_data, and active conversation states; bot_data and callback_data are not persisted.



The default persistence file is state/magistrcheckbot_state.pickle, relative to the bot working directory, configured in [magister_checking/bot/config.py](d:\MyActivity\MyInfoBusiness\MyPythonApps\12%20MagisterProjectChecking\magister_checking\bot\config.py).



The bot uses one persistent ConversationHandler(name="registration", persistent=True, allow_reentry=True, per_message=True) in [magister_checking/bot/app.py](d:\MyActivity\MyInfoBusiness\MyPythonApps\12%20MagisterProjectChecking\magister_checking\bot\app.py). Numeric state IDs and user_data keys are part of the persistence contract, as documented in [docs/fsm_stabilization_notes.md](d:\MyActivity\MyInfoBusiness\MyPythonApps\12%20MagisterProjectChecking\docs\fsm_stabilization_notes.md).



Google Sheets remains the operational system of record for day-to-day workflow, while JSON snapshots are point-in-time exports, per [docs/contract_project_snapshot.md](d:\MyActivity\MyInfoBusiness\MyPythonApps\12%20MagisterProjectChecking\docs\contract_project_snapshot.md).



A full row check with apply=True writes current Stage 2/3/4 outputs to the registration sheet, appends a row to История проверок, then attempts to upload a JSON snapshot to Drive in [magister_checking/row_check_cli.py](d:\MyActivity\MyInfoBusiness\MyPythonApps\12%20MagisterProjectChecking\magister_checking\row_check_cli.py).



Snapshot upload creates a new JSON file per configured Drive folder and logs per-folder failures without failing the user-visible row check, in [magister_checking/snapshot_drive.py](d:\MyActivity\MyInfoBusiness\MyPythonApps\12%20MagisterProjectChecking\magister_checking\snapshot_drive.py).

flowchart TD
    telegramBot["Telegram bot"] --> persistenceFile["PicklePersistence file"]
    telegramBot --> registrationSheet["Google Sheet: Registration"]
    rowCheck["run_row_check apply=True"] --> registrationSheet
    rowCheck --> historySheet["Google Sheet: History"]
    rowCheck --> snapshotJson["Drive JSON snapshots"]
    registrationSheet --> snapshotJson
    historySheet --> onlyIfChanged["only_if_changed fingerprint gate"]

Data Classification

Canonical data:





Current operational registration state: the Регистрация worksheet. This is the day-to-day system of record for row status, Telegram binding, FIO, phone, supervisor fields, report URL, check outputs, fill_status, and manual link override columns.



Role/admin/supervisor lists in Google Sheets where those sheets are used by the bot.



Current active bot conversation state: the PicklePersistence file. It is canonical only for in-progress Telegram conversations, pending admin prompts, PIN context, role claim state, and active FSM position. It is not canonical for project facts after they have been written to the sheet.



Snapshot semantic model at a point in time: each JSON snapshot is canonical for the frozen artifact it represents, but not for current operational state if the sheet has moved on.

Reconstructable data:





Most current project snapshots can be rebuilt from the current sheet row plus the current row-check pipeline, as the snapshot contract says disagreements should normally be resolved by rebuilding from current pipeline inputs.



Human-facing Telegram/commission text can be re-rendered from stored JSON snapshots or regenerated from a fresh row check.



Check result columns can be recomputed by running the row check again, subject to current Google Drive/Docs availability and current source document contents.



Some История проверок gaps can be partially reconstructed from logs and Drive snapshot timestamps, but exact previous fingerprints and issue text are not fully reconstructable if the history row was never written or was edited.

Operationally critical data:





Регистрация and its header order/aliases, because row lookup and write-back depend on columns resolved from the header.



История проверок, because only_if_changed reads the last matching row by sheet order and fingerprint.



state/magistrcheckbot_state.pickle, because it restores active conversations after restart.



Service account JSON, bot token, .env, configured Drive snapshot folders, and the spreadsheet itself.



Drive source documents referenced by rows: intermediate reports, dissertation documents, project folders, LKB/publication links.



Bot logs for recovery diagnosis, because several failures are intentionally degradable and only visible operationally.

Current Operational Risks

PicklePersistence corruption:





A corrupt pickle can prevent the bot from restoring active conversations, and may prevent startup depending on how PTB fails while loading persistence.



There is no repository procedure for backing up or repairing magistrcheckbot_state.pickle.



broadcast.py reads the pickle directly with pickle.load and silently returns no recipients on read failure, so corruption can also affect broadcast reachability.



The path is relative to the working directory by default; starting the bot from another cwd can create or read a different state file.

Google Sheets accidental edits:





RAW write mode protects against formula execution, but it does not protect against a human editing, sorting, deleting, hiding, or renaming columns/rows.



Manual sorting or insertion in История проверок can change which history entry is treated as the latest for a row.



Edits to Регистрация can affect row identity, Telegram binding, manual overrides, or current fill_status.



Clean-write behavior intentionally clears check-result columns before writing fresh values. If a row check is run against the wrong row, the wrong row can be overwritten consistently and quickly.

Drive snapshot inconsistencies:





Snapshot upload is per-folder and non-transactional. One configured folder can succeed while another fails.



Latest snapshot selection is based on Drive modifiedTime among files named project_snapshot_r{row}_*.json; there is no manifest, checksum, or cross-folder quorum.



Snapshot upload failure does not fail the row check. That preserves user flow but creates possible sheet-history-snapshot divergence.



There is no retention or integrity policy in code; old snapshots accumulate until humans or Drive policy remove them.

Duplicate retries:





/справка, /recheck, callback buttons, and supervisor status checks can all trigger run_row_check(... apply=True).



only_if_changed prevents duplicate writes only when the current fingerprint matches the last matching history row. It does not protect forced full checks, concurrent checks, or history corruption.



There is no verified idempotency key across Telegram update/callback, row number, and fingerprint. Duplicate user actions may create duplicate history rows and duplicate snapshots.

Partial Stage failures:





Stage failures are represented in RowCheckReport and written to the sheet as current status; this is correct for business visibility.



History write failure is swallowed after sheet write. Snapshot upload failure is swallowed after sheet write and history attempt.



apply_row_check_updates writes values through one Sheets batch update and formatting through a separate spreadsheet batch update. A failure between those operations can leave values and strikethrough formatting temporarily inconsistent.



Dissertation metadata write is separate from main row-check writes and can fail independently.

Callback/persistence mismatch:





callback_data persistence is disabled. Inline callback payloads still exist in Telegram messages, but PTB callback cache is not persisted.



Some callback handlers are inside the persistent ConversationHandler states; recheck_button is registered globally outside the conversation handler.



After restart, a user may click an old inline button whose persisted FSM state no longer matches the message context, or whose context.user_data pending key has been cleared or changed.



Callback namespaces and state IDs are already documented as high-risk compatibility surfaces and should remain additive-only.

Bot restart during active conversations:





The restart can restore active FSM state if the same persistence file is available and compatible.



It cannot restore external operations that were in progress when the process died: an active row check, a partially completed Sheets write, a pending Drive upload, or an in-flight Telegram response.



Pending admin prompts and PIN/register/claim context may resume, but the human-visible Telegram message may be stale after a long downtime.



The PowerShell launcher prevents a second manual process, but this protection is script-level, not a Python-level lock. Other launch paths can still start two polling processes against the same token and same pickle.

Single Points Of Failure





The Google spreadsheet is the main operational SPOF: losing or corrupting it breaks registration, row checks, role lookups, history, and current status.



The PicklePersistence file is a SPOF for active conversations and pending bot UX, though not for already committed project data.



The service account credentials and Google API enablement are SPOFs for Sheets/Drive/Docs operations.



The Telegram bot token and one long-polling process are SPOFs for user access.



The configured Drive snapshot folder list is a SPOF for snapshot archive availability if only one folder is configured.



The local host/task scheduler environment is a SPOF for process availability and persistence/log file storage.



Source Drive documents are external SPOFs for reproducibility: if a student changes/deletes a document, old check results may not be exactly reproducible without stored snapshots.

Missing Recovery Procedures





No documented procedure to validate pickle readability before startup or after crash.



No documented procedure to recover from corrupt pickle while preserving the last good copy.



No documented procedure to reconcile Регистрация, История проверок, Drive snapshots, and bot logs after partial failure.



No documented procedure to recover from accidental sheet sort/delete/column rename.



No documented procedure to decide when to force full recheck versus trust only_if_changed.



No documented callback/session recovery guidance for users after restart.



No documented rollback sequence for bot release, config rollback, persistence rollback, and sheet rollback as one operational action.

Missing Backup Strategy





No explicit backup schedule for the persistence file.



No explicit backup/export schedule for the spreadsheet.



No retention policy for Drive snapshots.



No separate backup copy of .env, service account JSON, launch scripts, and Task Scheduler configuration.



No written restore drill cadence.



No immutable or restricted-access backup location is defined.

Missing Integrity Checks





No startup check that the configured persistence path is the expected absolute path and readable.



No periodic check that the spreadsheet has required worksheets and headers.



No check that История проверок is append-only and chronologically ordered for a row.



No cross-check that a successful applied check has sheet update, history row, and at least one snapshot when snapshot folders are configured.



No snapshot checksum/manifest to verify that uploaded JSON matches the generated payload.



No Drive folder consistency report across multiple configured snapshot folders.



No operational report for duplicate history rows with same row/fingerprint close in time.

Backup Policy

Persistence file:





Back up BOT_PERSISTENCE_FILE before every bot start, before every deployment, and on a timer while the bot is stopped or during a controlled quiescent window.



Keep atomic copies named with UTC timestamp, bot version/commit, and file size, for example magistrcheckbot_state.2026-05-07T05-00-00Z.<commit>.pickle.



Retention: hourly for 48 hours, daily for 30 days, weekly for 12 weeks, monthly for 12 months.



Store at least one copy outside the repo working tree and outside the same local disk if possible.



Never commit pickle backups to Git.

Google Sheets:





Enable Google Drive version history and restricted edit access for the spreadsheet.



Export the full spreadsheet at least daily to an operator-owned backup folder, preferably as .xlsx plus Google-native copy.



Export immediately before bulk admin operations, deployment, or manual structural changes.



Retention: daily for 30 days, weekly for 12 weeks, monthly for 12 months.



Protect the header row, formula/dashboard areas, История проверок, and bot-owned result columns from casual editing.

Drive snapshots:





Keep the current append-only snapshot behavior.



Configure at least two Drive snapshot folders if operationally possible: primary working archive and restricted backup archive.



Retention by row: keep all snapshots for the active semester/attestation period; after closure keep the first, last, and every status-changing snapshot, plus monthly samples if volume is high.



Minimum retention: all snapshots for 12 months; final commission snapshots for the institutional retention period.



Do not rename existing project_snapshot_r{row}_*.json files, because discovery depends on that pattern.

Secrets and runtime configuration:





Back up .env, service account JSON, Task Scheduler task export, and launcher scripts in a restricted secret backup location.



Rotate and reissue credentials through documented steps, not by editing historical backups.



Keep a redacted runbook copy that lists which env vars must exist without containing secrets.

Logs:





Preserve current rotated bot logs for at least 30 days as already configured.



For DR, copy logs to backup with the same cadence as persistence during incident windows.



Treat logs as sensitive because they can contain operational identifiers and exception context.

Restore Strategy

Restore priority order:





Restore service availability first: one bot process, correct .env, correct service account, correct working directory, correct persistence path.



Restore canonical data next: spreadsheet and current Drive source access.



Restore active UX last: persistence file and user prompts.

Spreadsheet restore:





If accidental edits are recent and localized, use Google Sheets version history to restore only the affected range where possible.



If the structure is damaged, restore a full spreadsheet copy, then verify worksheet names, headers, protected ranges, and service account permissions before pointing production at it.



After restore, run a read-only operational check: required worksheets, required columns, sample row load, and history lookup for representative rows.



For rows affected during the incident, force a full row check rather than relying on only_if_changed, because history/order may be compromised.

Persistence restore:





Stop the bot and confirm no second bot process is running.



Copy the current pickle aside as an incident artifact, even if corrupt.



Validate candidate backup readability in an isolated check before replacing the active file.



Restore the newest readable backup that predates the incident and matches the current FSM contract.



Start the bot from the known repo root or with an absolute BOT_PERSISTENCE_FILE.



Instruct users with stale conversations to use /start or /выход if their restored prompt no longer matches the visible Telegram message.

Snapshot restore:





If snapshots are missing but the sheet is intact, regenerate snapshots from current sheet state via the existing row-check/snapshot generation path.



If Drive folders diverged, treat the folder with the most complete set as source for backfill; do not hand-edit JSON by default.



If sheet and snapshot disagree, current policy is to rebuild from the sheet/current pipeline unless the incident specifically proves the sheet was corrupted and the snapshot is the intended historical record.



Preserve inconsistent or duplicate snapshots as evidence until the incident is closed.

History restore:





If История проверок was sorted or edited, restore from spreadsheet version history first.



If exact history cannot be restored, accept that only_if_changed has degraded reliability and run forced full checks for affected rows.



Rebuild only a minimal audit note if needed; do not fabricate exact fingerprints unless recovered from logs/snapshots.

Persistence Recovery Procedures

Normal restart:





Stop bot cleanly.



Back up the pickle.



Start with the expected cwd or absolute persistence path.



Verify logs show bot startup and no persistence load errors.

Corrupt pickle:





Stop bot.



Move the active pickle to an incident quarantine name.



Restore newest validated backup.



Start bot and monitor first user interactions.



If no readable backup exists, start with an empty persistence file and notify operators that active conversations are lost but sheet data remains intact.



For affected users, use /start or /выход to create a clean conversation state.

Persistence/schema mismatch after release:





Stop bot immediately.



Roll back code to the last release compatible with the current pickle.



Restore the pre-deploy pickle backup if the new release wrote incompatible state.



Do not attempt ad hoc pickle editing except as a last-resort offline forensic operation with the original file preserved.

Duplicate process / concurrent pickle access:





Stop all bot processes.



Preserve the current pickle and logs.



Restore the latest pre-incident pickle if corruption or state anomalies are observed.



Restart only one process through the approved launcher.

Safe Rollback Procedures

Before deployment:





Record commit/version, env hash/redacted config summary, persistence file path, spreadsheet ID, snapshot folder URLs.



Stop bot or put it into a controlled quiet period.



Back up pickle, .env, service account file reference, and spreadsheet.



Confirm no long-running row check is in progress.

Code rollback:





Stop bot.



Revert to previous known-good release.



Restore pre-deploy pickle if FSM state IDs, handler names, callback namespaces, or user_data keys were touched.



Keep the spreadsheet as-is unless the release performed bad writes. If sheet writes are bad, restore affected ranges from version history.



Start bot and force full checks for rows touched during the failed release.

Config rollback:





Restore previous .env/Task Scheduler settings.



Verify BOT_PERSISTENCE_FILE, spreadsheet ID, worksheet name, and snapshot folder URLs.



Confirm Google API/service account access with read-only checks before resuming user traffic.

Data rollback:





Prefer range-level sheet restore over full spreadsheet rollback when possible.



Preserve history/snapshot evidence before deleting duplicates or bad rows.



If history was corrupted, temporarily disable operational reliance on only_if_changed for affected rows by using full checks.

Spreadsheet Protection Strategy

Access control:





Give edit access only to operators who need it; give most users view/comment access.



Keep service account as editor on required sheets and Drive folders.



Use a separate backup owner account or restricted Shared Drive for backups.

Protected ranges:





Protect header rows in all bot-managed sheets.



Protect История проверок from manual edits; if humans need audit notes, use a separate notes sheet.



Protect bot-owned result columns in Регистрация: link validation, accessibility, extracted links, Stage 4 metrics, compliance, fill_status, and internal Telegram binding columns.



Allow manual edit only in intended input columns: FIO/contact fields, report URL, and documented manual override columns.

Operational rules:





Do not sort История проверок in place. Use filtered views instead.



Do not insert columns in the middle of bot-managed ranges without a compatibility check.



Before bulk edits, create a named spreadsheet version/export.



For manual correction, record the row number, operator, reason, before/after values, and whether a full recheck was run.

Integrity checks:





Daily: verify required worksheet names and headers.



Daily: verify История проверок header and that new rows are append-only.



Daily: sample latest snapshots for recent changed rows and verify each JSON parses and row number matches filename.



Weekly: compare snapshot counts across configured Drive folders.



Incident-time: find duplicate history rows with same row/fingerprint close in time and decide whether they are harmless duplicate retries or evidence of concurrent processing.

Handling Specific Incident Types

Pickle corruption:





Impact: active conversations and pending prompts may be lost or stale; committed sheet data remains canonical.



Recovery: restore last readable pickle backup; if unavailable, start empty and instruct users to restart flows.



Verification: bot starts, /start works, admin prompt state is clean, no persistence load errors.

Accidental Google Sheets edits:





Impact: can corrupt canonical operational data and only_if_changed decisions.



Recovery: restore range/version, then force full checks for affected rows.



Verification: headers intact, row lookup works, history latest row is correct, snapshots can be rebuilt.

Drive snapshot inconsistency:





Impact: commission/archive artifacts may be incomplete or divergent, while sheet can remain correct.



Recovery: backfill missing snapshots from current sheet or copy from complete folder; preserve bad/missing evidence.



Verification: latest snapshot per row parses, row number matches filename/content, configured folders have expected copies.

Duplicate retries:





Impact: duplicate history rows/snapshots; possible repeated clean-writes of same row.



Recovery: usually preserve duplicates as audit unless they caused wrong-row writes. For affected rows, compare sheet current values with latest intended report and rerun full check if needed.



Verification: one current sheet state, latest history fingerprint understood, duplicate snapshots marked as harmless or superseded.

Partial Stage failures:





Impact: sheet may correctly show failed/stopped status, but history/snapshot may be missing; formatting may lag values if formatting write failed.



Recovery: rerun full check after upstream Google/Drive issue is resolved. If sheet values were written but history/snapshot failed, rerun full check to regenerate audit artifacts.



Verification: history_write=success, snapshot_upload_count expected, sheet values and formatting align.

Callback/persistence mismatch:





Impact: old inline buttons or pending prompts can route incorrectly after restart or rollback.



Recovery: prefer user-level reset via /выход or /start; admin pending prompts should be reissued.



Verification: callback handlers answer or safely reject stale interactions; no wrong row operation occurs without explicit row resolution.

Bot restart during active conversations:





Impact: active prompts may resume; in-flight row checks may be interrupted between side effects.



Recovery: inspect logs around shutdown/startup; reconcile any rows with started-but-not-completed checks; tell users to restart stale flows.



Verification: no duplicate bot process, persistence restored, incident rows reconciled.

Minimal-Risk Incremental Hardening Roadmap

Phase 0: Operational runbook only





Document backup/restore procedures for pickle, spreadsheet, snapshots, secrets, and logs.



Define operator rules for protected ranges, filtered views, manual corrections, and forced full checks.



Add a release checklist that always backs up persistence and exports the spreadsheet before deploy.

Phase 1: No-behavior integrity checks





Add or run read-only checks for required worksheets, headers, persistence readability, snapshot folder access, and recent snapshot JSON parseability.



Produce a daily operator report: backups present, latest pickle backup age, spreadsheet export age, snapshot folder consistency, duplicate recent history rows.



Keep these checks outside the main bot behavior initially.

Phase 2: Safer operations around current persistence





Make BOT_PERSISTENCE_FILE absolute in production configuration.



Add controlled pre-start backup and readability validation in the launcher/runbook.



Ensure only the approved launcher/task can run production polling; keep the existing script-level single-process guard and document it as mandatory.

Phase 3: Sheet and snapshot guardrails





Apply protected ranges and edit permissions.



Add snapshot retention/archival procedure that preserves naming compatibility.



Add reconciliation procedure for applied row checks where sheet/history/snapshot are not all present.

Phase 4: Additive diagnostics and idempotency evidence





Add log-only correlation for Telegram update/callback, row number, fingerprint, history write, and snapshot upload result.



Detect duplicate retries operationally before changing behavior.



Do not change only_if_changed semantics until logs prove the safe cases.

Phase 5: Compatibility-safe hardening





Add optional integrity fields to future snapshots only in a backward-compatible way.



Add tests/runbook checks around FSM persistence compatibility before releases that touch state IDs, handler name, callback namespaces, or user_data keys.



Keep PicklePersistence, Google Sheets, Drive snapshots, history, and routing contracts unchanged until a separate migration project is approved.

Explicit Non-Goals





Do not replace PicklePersistence in this phase.



Do not introduce an external database.



Do not redesign the FSM or split the current ConversationHandler.



Do not rename callback namespaces or snapshot files.



Do not change the current only_if_changed, history, or snapshot discovery contracts as part of DR hardening.



Do not hand-edit JSON snapshots as the normal recovery path.

