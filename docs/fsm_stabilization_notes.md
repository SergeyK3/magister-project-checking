# FSM Stabilization Notes

Данный документ фиксирует:

* фактическую PTB FSM архитектуру;
* persistence constraints;
* compatibility constraints;
* безопасные правила эволюции FSM.

Документ дополняет:

* docs/updated_fsm.md
* docs/diagrams/fsm_overview.puml
* docs/domain_model.md

и описывает operational limitations текущей реализации.

---

# 1. Canonical FSM Architecture

Текущая FSM реализована одним persistent `ConversationHandler`.

Источник:

* `magister_checking/bot/app.py`
* `magister_checking/bot/handlers.py`

Основные параметры:

```python
ConversationHandler(
    name="registration",
    persistent=True,
    allow_reentry=True,
    per_message=True,
)
```

FSM НЕ разделена на отдельные registration/admin/retry conversations.

Все сценарии работают внутри одного общего `ConversationHandler`.

---

# 2. Numeric State Constraints

## Critical Rule

Numeric state ids являются частью persistence contract.

Из-за использования `PicklePersistence` запрещено:

* перенумеровывать states;
* переиспользовать numeric ids;
* удалять active states без migration strategy.

---

## Frozen Numeric States

| State                     | Numeric ID |
| ------------------------- | ---------- |
| ASK_FIELD                 | 0          |
| ASK_CONFIRM               | 1          |
| BIND_ASK_FIO              | 2          |
| BIND_CONFIRM              | 3          |
| PROJECT_CARD_ASK_TARGET   | 4          |
| SPRAVKA_MENU              | 5          |
| SPRAVKA_ASK_TARGET        | 6          |
| ROLE_PICK                 | 7          |
| CLAIM_ASK_FIO             | 8          |
| CLAIM_CONFIRM             | 9          |
| STUDENT_MSG_ASK_TARGET    | 10         |
| STUDENT_MSG_PICK_KIND     | 11         |
| STUDENT_MSG_ASK_EXTRA     | 12         |
| STUDENT_MSG_ASK_CUSTOM    | 13         |
| STUDENT_MSG_CONFIRM       | 14         |
| STUDENT_MSG_BULK_ASK_ROWS | 15         |
| STUDENT_MSG_BULK_CONFIRM  | 16         |
| PIN_VERIFY_INPUT          | 17         |

---

# 3. Conceptual FSM vs PTB FSM

## Important Distinction

Документы:

* `docs/updated_fsm.md`
* `docs/diagrams/fsm_overview.puml`

описывают conceptual/business FSM.

Они НЕ являются точным отражением PTB numeric states.

---

## Conceptual-Only States

Следующие состояния существуют только как conceptual documentation:

* STATE_UNREGISTERED
* STATE_WAITING_FIO
* STATE_ROLE_DETECTED
* STATE_MAIN_MENU
* STATE_RUNNING_CHECK
* STATE_SHOW_REPORT
* STATE_WAITING_ADMIN_MESSAGE
* STATE_CONFIRM_ADMIN_MESSAGE
* STATE_SENDING_ADMIN_MESSAGE
* STATE_ACCESS_DENIED

Они НЕ являются persistence states PTB.

---

# 4. Persistence Constraints

## Current Persistence

Используется:

```python
PicklePersistence
```

Источник:

* `magister_checking/bot/app.py`

---

## Persistence Includes

Сохраняются:

* user_data
* chat_data
* active conversation states

---

## Dangerous Areas

Особенно опасно менять:

* state numbers
* ConversationHandler.name
* callback namespaces
* user_data keys
* retry context keys
* PIN flow keys
* role claim keys

---

# 5. Compatibility Layer

## Public Canonical Commands

Основной V3 UX:

* `/start`
* `/справка`
* `/выход`
* `/help`

---

## Compatibility Aliases

Сохраняются как compatibility-only:

* `/spravka`
* `/cancel`
* `/register`
* `/recheck`

---

## Internal / Admin Compatibility

Internal-only commands:

* `/project_card`
* `/student_message`
* `/student_message_bulk`
* `/stats`
* `/sync_dashboard`
* `/sync_magistrants`

---

# 6. Russian Command Aliases

Русские команды реализованы НЕ отдельной FSM, а regex MessageHandler aliases.

Примеры:

* `/старт`
* `/справка`
* `/выход`

---

## Important Constraint

Русские aliases должны оставаться additive-only.

Запрещено:

* удалять латинские handlers;
* менять callback namespaces;
* менять fallback semantics.

---

# 7. Retry / Recheck Constraints

## Current Canonical Retry UX

Canonical public retry flow:

```text
/справка
```

---

## Legacy Compatibility

Следующие маршруты остаются compatibility-only:

* `/recheck`
* `recheck:full`
* `recheck:full:<row>`

---

## Important Constraint

Inline callback namespaces пока НЕ должны изменяться.

---

# 8. Snapshot Constraints

## Current Behavior

Retry pipeline использует:

* fingerprint comparison;
* history checks;
* snapshot reuse logic.

---

## Important Constraint

Повторный `/справка` НЕ должен:

* создавать duplicate snapshots;
* создавать duplicate history rows;
* менять fill_status без изменений.

---

# 9. Safe Migration Rules

## Allowed

Разрешено:

* additive aliases;
* helper extraction;
* comments/docstrings;
* documentation cleanup;
* regression tests;
* compatibility wrappers.

---

## Forbidden

Запрещено:

* state renumbering;
* ConversationHandler split;
* persistence rewrite;
* callback namespace rewrite;
* hard removal compatibility handlers;
* changing retry semantics.

---

# 10. Safe Cleanup Strategy

## Recommended Sequence

### Phase A

Characterization tests.

### Phase B

Documentation clarification.

### Phase C

Compatibility annotation.

### Phase D

Interrupt alias hardening.

### Phase E

Pure helper extraction.

### Phase F

Long-term compatibility retirement.

---

# 11. Helper Extraction Strategy

## Recommended Future Structure

```text
registration_flow.py
role_claim_flow.py
spravka_flow.py
admin_message_flow.py
compat_handlers.py
```

---

## Important Constraint

ConversationHandler должен оставаться:

* единым;
* persistent;
* numerically stable.

---

# 12. Fallback Ordering Constraints

## Current Behavior

Fallback ordering сейчас intentionally prioritizes:

1. exit
2. restart
3. retry/check
4. admin shortcuts

---

## Important Risk

Broad text handlers:

```python
filters.TEXT & ~filters.COMMAND
```

могут перехватывать command-like text.

---

## Safe Future Improvement

Разрешено:

* additive regex command handlers;
* interrupt guards before broad text handlers.

Запрещено:

* reorder without regression tests.

---

# 13. High-Risk Areas

Особенно опасно трогать:

* `_do_recheck`
* PIN flow
* callback_data namespaces
* persistence keys
* role claim flow
* retry pipeline
* active admin dialogs
* snapshot generation
* inline recheck buttons

---

# 14. Current Architectural Direction

Текущая стратегия проекта:

```text
stabilize first
refactor second
rewrite never
```

---

# 15. Operational Principle

FSM evolution должна быть:

* additive-first;
* compatibility-safe;
* persistence-aware;
* regression-tested;
* rollback-friendly.

Big-bang FSM rewrite запрещён.
