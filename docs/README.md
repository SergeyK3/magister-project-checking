# Magistr Checker — Architecture Documentation Index

Данный каталог содержит:

* архитектурные документы;
* FSM и routing ограничения;
* migration notes;
* diagrams;
* AI-agent operational constraints.

Документы ниже являются canonical reference для Cursor Agent и будущих migration/refactoring phases.

---

# 1. Core Architecture Documents

| File                                            | Purpose                                                               |
| ----------------------------------------------- | --------------------------------------------------------------------- |
| `domain_model.md`                               | Доменная модель Telegram-бота, ролей, регистраций и snapshot pipeline |
| `architecture_notes.md`                         | Технические решения, ограничения и архитектурные заметки              |
| `commands.md`                                   | Canonical public commands V3                                          |
| `deprecated_commands.md`                        | Legacy/compatibility commands                                         |
| `updated_fsm.md`                                | Conceptual/business FSM                                               |
| `fsm_stabilization_notes.md`                    | Реальные PTB FSM constraints и persistence limitations                |
| `tz_fragment_telegram_group_auto_approve_ru.md` | Фрагмент ТЗ по Telegram onboarding/approval                           |
| `guide_admin_ruV3.md`                           | Руководство администратора                                            |
| `guide_nauchrukV3.md`                           | Руководство научного руководителя                                     |
| `guide_userV3.md`                               | Руководство магистранта                                               |

---

# 2. Diagrams

Каталог:

```text id="jlwnra"
docs/diagrams/
```

Содержит:

* PlantUML diagrams;
* architecture views;
* FSM diagrams;
* retry/snapshot схемы;
* Google Sheets structure.

---

## Important Distinction

`updated_fsm.md` и `.puml` diagrams описывают conceptual FSM.

Фактическая PTB FSM реализована:

* одним persistent `ConversationHandler`;
* numeric states;
* PicklePersistence.

Canonical operational constraints:

```text id="jlwnrb"
fsm_stabilization_notes.md
```

---

# 3. Current Public UX (V3)

Canonical public commands:

| Command    | Purpose                       |
| ---------- | ----------------------------- |
| `/start`   | Вход в систему                |
| `/справка` | Retry/check/report entrypoint |
| `/выход`   | Завершение активного диалога  |
| `/help`    | Справка                       |

---

# 4. Compatibility Layer

Compatibility-only commands сохраняются для:

* legacy routing;
* inline callback compatibility;
* старых workflow.

Документ:

```text id="jlwnrc"
deprecated_commands.md
```

---

## Important Rule

Compatibility handlers:

* НЕ удаляются без migration phase;
* НЕ удаляются без regression tests;
* НЕ удаляются без callback migration strategy.

---

# 5. Current FSM Architecture

## Current State

FSM реализована:

* одним `ConversationHandler`;
* persistent=True;
* PicklePersistence;
* frozen numeric states.

---

## Important Constraints

Запрещено:

* renumber FSM states;
* split ConversationHandler;
* rewrite persistence;
* rewrite callback namespaces;
* change retry semantics.

Подробности:

```text id="jlwnrd"
fsm_stabilization_notes.md
```

---

# 6. Retry / Snapshot Architecture

Canonical retry UX:

```text id="jlwnre"
/справка
```

Legacy compatibility:

```text id="jlwnrf"
/recheck
recheck:full
recheck:full:<row>
```

---

## Important Rule

Retry pipeline:

* fingerprint-aware;
* snapshot-aware;
* history-aware.

Повторная проверка без изменений НЕ должна:

* создавать duplicate snapshots;
* создавать duplicate history rows.

---

# 7. Persistence Architecture

Persistence:

```text id="jlwnrg"
PicklePersistence
```

Сохраняются:

* FSM states;
* user_data;
* chat_data.

---

## Critical Rule

Persistence format считается production contract.

Любые изменения требуют:

* migration strategy;
* regression tests;
* rollback plan.

---

# 8. Current Migration History

| Phase    | Description                               |
| -------- | ----------------------------------------- |
| Phase 0  | Characterization tests и inventory freeze |
| Phase 1  | Public routing migration                  |
| Phase 2  | `/справка` retry consolidation            |
| Phase 3A | Safe helper extraction                    |

---

# 9. AI Agent Constraints

## Forbidden

AI Agents НЕ должны:

* renumber FSM states;
* split ConversationHandler;
* rewrite persistence;
* rewrite callback_data namespaces;
* remove compatibility handlers aggressively;
* change retry semantics;
* rewrite Google Sheets schema;
* perform big-bang refactoring.

---

## Allowed

AI Agents МОГУТ:

* add helper modules;
* improve documentation;
* add regression tests;
* improve comments/docstrings;
* add additive aliases;
* improve architecture diagrams;
* extract pure helper functions.

---

# 10. Current Architectural Strategy

Current project strategy:

```text id="jlwnrh"
stabilize first
refactor second
rewrite never
```

---

# 11. Recommended Future Directions

Безопасные направления развития:

* helper extraction;
* observability/logging;
* retry UX improvements;
* snapshot service extraction;
* admin workflow cleanup;
* Sheets repository cleanup;
* metrics/monitoring.

---

## NOT Recommended

В текущем состоянии НЕ рекомендуется:

* FSM rewrite;
* persistence rewrite;
* multi-conversation decomposition;
* callback namespace migration;
* aggressive command removal.

---

# 12. Operational Principle

Любая архитектурная эволюция должна быть:

* additive-first;
* compatibility-safe;
* persistence-aware;
* regression-tested;
* rollback-friendly.

Big-bang refactoring запрещён.
