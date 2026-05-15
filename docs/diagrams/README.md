# Diagrams

Данный каталог содержит архитектурные диаграммы проекта проверки магистерских проектов.

Диаграммы являются частью architecture context и используются:

* разработчиками;
* Cursor Agent;
* AI-assisted refactoring;
* architectural review.

---

# Основные принципы

* Markdown-документы являются source of truth.
* PlantUML используется как visual projection architecture.
* Диаграммы должны соответствовать актуальному FSM и business logic.
* После изменения FSM необходимо обновлять соответствующие `.puml`.

---

# Список диаграмм

## fsm_overview.puml

FSM-состояния Telegram-бота.

Отражает:

* состояния;
* transitions;
* dialog states.

Canonical source:

```text id="8bhp0t"
docs/updated_fsm.md
```

---

## identity_resolution.puml

Алгоритм идентификации пользователя.

Отражает:

* поиск Telegram ID;
* fallback на ФИО;
* role detection;
* Telegram ID binding;
* routing по ролям.

Canonical source:

```text id="8w5vbi"
docs/updated_fsm.md
docs/domain_model.md
```

---

## dissertation_pipeline.puml

Pipeline проверки проекта.

Отражает:

* этапы проверки;
* validation order;
* snapshot generation;
* report generation.

Canonical source:

```text id="oddyc0"
docs/architecture_notes.md
docs/domain_model.md
```

---

## role_routing.puml

Распределение команд по ролям.

Отражает:

* команды магистранта;
* команды научрука;
* команды администратора.

Canonical source:

```text id="ln88lr"
docs/commands.md
```

---

## sheets_schema.puml

Логическая схема Google Sheets.

Отражает:

* основные листы;
* ключевые поля;
* persistence structure.

Canonical source:

```text id="d0psik"
docs/domain_model.md
```

---

## retry_model.puml

Модель повторной проверки.

Отражает:

* retry logic;
* reuse existing rows;
* snapshot update strategy;
* idempotency.

Canonical source:

```text id="aj4f1l"
docs/domain_model.md
docs/updated_fsm.md
```

---

## snapshot_architecture.puml

Архитектура snapshot subsystem.

Отражает:

* pipeline stages;
* snapshot generation;
* report generation;
* persistence integration.

Canonical source:

```text id="7uy9k6"
docs/domain_model.md
```

---

## system_context.puml

Контекст системы верхнего уровня.

Отражает:

* Telegram Bot;
* Google APIs;
* Google Sheets;
* JSON snapshots;
* основные роли.

Canonical source:

```text id="fpjlwm"
docs/architecture_notes.md
```

---

# Важно

Диаграммы не заменяют Markdown-документы.

Markdown остаётся:

* canonical architecture description;
* source of truth.

PlantUML используется:

* для визуализации;
* для AI context;
* для architectural navigation.
