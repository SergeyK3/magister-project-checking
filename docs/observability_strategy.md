Стратегия Observability Для Telegram-Бота

Подтвержденное Текущее Состояние





В magister_checking/bot/app.py уже есть базовая инфраструктура: текстовый stream-лог и JSON-файл через TimedRotatingFileHandler, ротация за 30 дней, поля ts, level, logger, module, func, lineno, message, exc_info.



httpx, httpcore, telegram.ext.Updater, telegram.bot подняты минимум до WARNING, чтобы не утекал TELEGRAM_BOT_TOKEN; googleapiclient.http поднят до ERROR, поэтому диагностировать Google API нужно собственными логами приложения.



В magister_checking/bot/handlers.py логи в основном точечные: _record_action на DEBUG, отдельные info при PIN-less сценариях, warning/exception при сбоях Dashboard, магистрантов, /stats, /spravka, /recheck, JSON snapshot.



В magister_checking/bot/pin_verify.py уже есть JSON-подобные события pin_issued и pin_verify, но они логируются как строка внутри message и содержат чувствительные поля pin_plaintext, phone_normalized, telegram_id.



В magister_checking/row_check_cli.py есть важные silent paths: only_if_changed при совпавшем fingerprint возвращает unchanged=True без лога; ошибка append_recheck_history гасится через pass; _prefetch_drive_file_mimes, _get_drive_modified_time, _try_load_report_document, _try_load_dissertation_metrics_and_meta скрывают часть Google-сбоев как допустимый degradable flow.



В magister_checking/snapshot_drive.py логируется успешная загрузка snapshot и ошибки Drive, но пустая конфигурация папок возвращает [] без записи.



В magister_checking/drive_latest_snapshot.py логируются подготовка Drive и плохие folder URL, но не логируется результат выбора: найден файл, не найден файл, сколько кандидатов просмотрено.



В magister_checking/bot/report_enrichment.py Google Docs ошибки уже классифицируются для пользователя, включая SERVICE_DISABLED / docs.googleapis.com, но в логах нет нормализованных полей api, method, status, reason, service.

Недостающие Логи

Операционные логи:





Старт и завершение ключевых команд: /start, /register, /spravka, /recheck, /stats, /sync_dashboard, /sync_magistrants, /project_card, /student_message, /student_message_bulk.



Итог регистрации/привязки роли: создана строка, обновлена строка, найдена существующая строка, пользователь не найден в листе, admin/supervisor/student branch.



Результат синхронизации Dashboard/магистрантов: success, skipped by config, failed with reason.

Retry diagnostics:





Вход в _do_recheck: trigger, source, row resolution method, only_if_changed, history_source, row_number_override.



Выход из run_row_check: success, unchanged, failed, stopped_at, stage summary, apply, duration.



Silent paths: совпавший fingerprint, ошибка записи истории, отсутствие snapshot output folder, успешная/неуспешная загрузка snapshot.

FSM transition logs:





Логировать не весь PTB internals, а возврат каждого handler-level шага: from_state, to_state, handler, reason, conversation=name=registration.



Начать с верхнеуровневых entry/fallback/state handlers в handlers.py, не меняя ConversationHandler и не добавляя новую FSM-абстракцию.



Для ConversationHandler.END логировать to_state=end и end_reason.

Google API diagnostics:





Нормализовать ошибки Google в приложении: api=sheets|drive|docs, method, status, reason, service, quota_metric если доступен, resource_kind, resource_id_hash, operation.



Отдельно различать docs.googleapis.com SERVICE_DISABLED и ACL/permission errors. Если в том же прогоне Drive/Sheets успешны, не описывать это как отсутствие связи с Google.



Не включать полный URL документа по умолчанию; хранить file_id_hash или короткий file_id_tail только для админского DEBUG при необходимости.

Callback diagnostics:





Логировать callback receive/handled/rejected для start:*, spravka:*, recheck:*, student reminder template/confirm/bulk.



Поля: callback_family, callback_action, callback_data_hash или whitelisted action, is_admin, chat_type, message_id, answer_result, edit_markup_result.



Не логировать произвольный callback_data как raw, если в будущем туда могут попасть payload/PII.

Категории Структурированных Событий





bot.lifecycle: старт приложения, конфигурация лог-файла включена/выключена, имя таблицы/листа без секретов.



telegram.update: команда или callback получены, handler выбран, приватный/групповой чат, rejected reason.



fsm.transition: handler-level переходы между состояниями регистрации и сервисных сценариев.



registration.flow: создание/обновление анкеты, resume existing row, bind, claim admin/supervisor.



pin.verify: выпуск/проверка PIN без plaintext PIN и без полного телефона в production.



retry.row_check: вход/выход /spravka и /recheck, duration, unchanged/full, stage summary.



snapshot.drive: build/upload/pick/download snapshot, result, folder configured/missing, candidates count.



google.api: Drive/Sheets/Docs операции и нормализованные ошибки.



admin.ops: /stats, sync, project card, admin JSON snapshot parse/render.



notify.telegram: отправка сообщений/документов, Telegram RetryAfter, BadRequest, network errors.



security.privacy: события, где intentionally redacted/suppressed sensitive field.

Безопасные Поля

Production-safe по умолчанию:





event, category, operation, status, duration_ms, attempt, retry_policy, handler, command, callback_family, callback_action.



trace_id, update_id, session_id, conversation_id, row_number, role, is_admin, chat_type.



api, method, http_status, error_reason, google_service, quota_or_rate_limited, stage, stopped_at, unchanged.



snapshot_present, snapshot_upload_count, snapshot_pick_result, history_source, fingerprint_hash_prefix.

Условно безопасно только при ограниченном доступе к логам:





telegram_id, chat_id, message_id, spreadsheet_id, worksheet_name, folder_id_tail, file_id_tail.



Лучше использовать stable hash: telegram_user_hash, chat_hash, file_id_hash, оставляя raw id только на DEBUG или в локальном аварийном режиме.

Не логировать в production raw:





TELEGRAM_BOT_TOKEN, service account JSON, access tokens, refresh tokens.



pin_plaintext, введенный PIN, полный телефон, ФИО, username, first/last name.



Полные Google Docs/Drive URLs, содержимое отчёта, snapshot JSON, PDF/HTML справку, тексты пользовательских сообщений и произвольные callback payload.



Полный traceback в Telegram-алерте может содержать контекст исключения; оставить в файле лога с ограниченным доступом, а алертам дать укороченный sanitized summary.

Correlation Identifiers





trace_id: генерируется на один inbound update/command/callback; пробрасывается через handler, _do_recheck, run_row_check, snapshot upload/pick, Google diagnostics.



session_id: стабильный на conversation/session. Для текущей архитектуры достаточно hash(telegram_id + conversation_name) или сохраненного lightweight id в context.user_data; не использовать raw telegram id.



conversation_id: registration:<chat_hash> или registration:<user_hash> для PTB ConversationHandler(name="registration").



row_trace: row_number плюс source=telegram_id|admin_override|supervisor_status|callback_row.



snapshot_trace_id: trace_id + row_number + fingerprint_hash_prefix, чтобы связать retry, history и Drive JSON.



google_request_ref: локальный id операции, например trace_id:google:003, без попытки читать внутренний request id клиента.

Snapshot И Retry Tracing





На входе retry: retry.row_check.start с trigger=recheck|spravka|callback|supervisor_status, only_if_changed, apply, history_source, row_resolution.



После fingerprint: retry.fingerprint.computed с fingerprint_hash_prefix, report_file_id_hash, report_modified_time_present, counts of parsed Stage 3 urls.



При short-circuit: retry.row_check.unchanged на INFO, без stacktrace, с duration и last_history_found=true.



При полном прогоне: retry.row_check.completed на INFO со stopped_at, stage statuses, sheet_updated, history_write=success|failed|skipped, snapshot_upload=success|partial|failed|not_configured.



Ошибка append_recheck_history: WARNING, потому что flow продолжает работать, но диагностика retry деградирует.



Snapshot upload/pick: отдельные события для not_configured, invalid_folder_url, upload_success, upload_failed, pick_found, pick_not_found, download_failed.

User/Session Tracing





Для обычных production логов использовать user_hash, chat_hash, session_id, role, is_admin, row_number.



Raw telegram_id оставить только в ограниченном DEBUG/локальном режиме или в отдельном аварийном файле с коротким retention, если операционно необходимо.



Для админских действий логировать actor_role=admin, target_row_number, target_lookup=by_row|by_fio, но не raw ФИО.



Для студента логировать actor_role=student, own_row_found=true|false, registration_status, но не содержимое анкеты.

Рекомендуемые Уровни





DEBUG: подробные FSM transitions, callback raw-ish development details, sanitized Google request start/end, row resolution internals.



INFO: lifecycle, command/callback accepted, operation completed, retry start/end, unchanged short-circuit, snapshot uploaded/picked, admin sync success.



WARNING: recoverable degradation: Google 429, history write failed, snapshot folder missing/invalid, snapshot not found when expected, Telegram BadRequest/RetryAfter, Docs API SERVICE_DISABLED, ACL denied when operation can continue with user-facing warning.



ERROR: command/handler operation failed and user-visible operation did not complete; failed row check after retries; unhandled handler exception.



EXCEPTION: only when traceback is needed for unexpected failure. Не использовать для ожидаемых business validation failures.

Минимальный Риск Внедрения





Сначала договориться о схеме события и redaction policy без изменения поведения: helper для logger.info(..., extra=...) или JSON message adapter поверх текущего logging, совместимый с существующим _JsonLogFormatter.



Расширить файл-форматтер так, чтобы он включал whitelisted extra поля, но stream-формат оставить читаемым. Это сохраняет текущую архитектуру логирования.



Добавить только вход/выход logs вокруг handlers и _do_recheck, без изменения return states, retry policy, Google calls и UI.



Добавить silent-path diagnostics в run_row_check, snapshot upload/pick и Google wrappers: только логирование, без изменения исключений, retry, данных в таблице или сообщений пользователю.



Убрать/замаскировать самые рискованные текущие поля: pin_plaintext, полный телефон, ФИО+тел в duplicate-key warning. Если plaintext PIN пока нужен по UX, вынести это в явно локальный/админский режим с отдельной настройкой и предупреждением.



Включать категории поэтапно: сначала INFO для retry/snapshot/Google failures, затем FSM на DEBUG, затем callback diagnostics. После каждого этапа проверять объем логов и отсутствие PII.



Не подключать внешние observability-платформы до стабилизации схемы событий и redaction policy в локальном JSON-файле.

Что Не Делать В Этом Роллауте





Не менять бизнес-логику регистрации, retry, snapshot reuse, Google API fallback или callback routing.



Не переписывать ConversationHandler и не вводить новую FSM-модель.



Не добавлять новые retries поверх существующего пайплайна.



Не логировать содержимое документов, snapshot JSON, анкету, телефоны, PIN и полные ссылки.



Не включать verbose googleapiclient.http/telegram.bot на INFO в production, потому что текущий код уже защищает от токенов и шума через уровни логгеров.

