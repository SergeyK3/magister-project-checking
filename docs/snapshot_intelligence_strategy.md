Future Snapshot Intelligence Strategy

1. Текущая архитектура, которую нужно сохранить

Основа уже стабильна и должна остаться главным контуром:





Проверка строки запускается в magister_checking/row_check_cli.py: строка регистрации → загрузка промежуточного отчета → парсинг ссылок → вычисление fingerprint → при необходимости Stage 2/3/4 → обновление листа → история → JSON snapshot на Drive.



Бинарный only_if_changed сейчас срабатывает до HTTP/MIME/Stage 4: fingerprint считается из report_url, report_modifiedTime и четырех Stage 3 ссылок (project_folder_url, lkb_url, dissertation_url, publication_url). При совпадении с последней записью истории возвращается RowCheckReport(unchanged=True) без pipeline, без записи в лист, без истории и без нового snapshot.



История в magister_checking/bot/sheets_repo.py хранится в листе История проверок с фиксированными колонками, включая единственный fingerprint. Последняя запись выбирается по порядку строк листа, не по timestamp.



ProjectSnapshot в magister_checking/project_snapshot.py уже содержит schema_version, generated_at, identity, links, phases, metrics, stage3_extracted, unchanged, stopped_at, sheet_enrichment_metrics, provenance.source_fingerprint.



Последний snapshot для строки выбирается в magister_checking/drive_latest_snapshot.py среди файлов project_snapshot_r{row}_*.json по Drive modifiedTime.



Stage 3 в magister_checking/bot/row_pipeline.py производит не отдельный файл, а StageResult и Stage3CellUpdate: нормализованные значения ссылок, нет, strikethrough, warnings. MIME целевых Drive-файлов используется внутри прогона, но отдельно в snapshot/history сейчас не сохраняется.

flowchart TD
    rowCheck["run_row_check"] --> parseReport["Parse report and merge sheet links"]
    parseReport --> sourceFingerprint["Current source fingerprint"]
    sourceFingerprint --> historyCompare["Compare with last history row"]
    historyCompare -->|"same and only_if_changed"| unchangedReturn["Return unchanged, no writes"]
    historyCompare -->|"different"| pipeline["Stage 2, Stage 3, Stage 4"]
    pipeline --> sheetUpdate["Update registration sheet"]
    sheetUpdate --> historyAppend["Append history row"]
    historyAppend --> snapshotBuild["Build ProjectSnapshot"]
    snapshotBuild --> driveUpload["Upload JSON snapshot"]

2. Ограничения текущего changed/unchanged

Текущее сравнение надежно отвечает только на вопрос: изменились ли известные входные ссылки или modifiedTime промежуточного отчета. Оно не доказывает, что диссертация содержательно та же самая.

Основные ограничения:





Содержимое диссертации не входит в fingerprint. Если URL диссертации тот же, а файл по ссылке заменили или обновили, only_if_changed может дать false negative и пропустить полный прогон.



MIME, размер, Drive revision/headRevision, checksum и modifiedTime целевого файла диссертации не сохраняются в fingerprint/history/snapshot как отдельные сигналы.



report_modifiedTime защищает только промежуточный отчет, и только когда удалось извлечь Drive file id.



Последняя история выбирается по последней строке листа; ручная сортировка или вставки могут изменить базу сравнения.



Stage 3 warnings и strikethrough полезны как результат классификации ссылок, но не позволяют отличить «тот же файл с другим содержанием» от «та же ссылка без изменений».

Риски false positives:





Изменился modifiedTime промежуточного отчета без содержательных изменений: форматирование, автосохранение, комментарии, служебное касание файла.



Ссылка была переписана в эквивалентную форму: иной URL того же Drive file id.



В лист вручную внесли ссылку с тем же смыслом, но другим синтаксисом.



Drive modifiedTime изменился из-за метаданных, не влияющих на проверку диссертации.

Риски false negatives:





Та же ссылка на диссертацию, но другой binary/content внутри файла.



Та же папка проекта, но внутри добавлены/заменены документы.



Изменился файл публикации или ЛКБ по той же ссылке, но report_url и сами ссылки не изменились.



Не удалось получить report_modifiedTime; тогда изменение промежуточного отчета при той же ссылке может не попасть в fingerprint.

Разделение технических и meaningful изменений:





Технические: timestamp, имя файла, эквивалентный URL, MIME уточнение, доступность временно упала/восстановилась, форматирование отчета без смены ссылок.



Meaningful: изменилась ссылка на диссертацию, изменилась фактическая revision/размер/checksum диссертации, изменились Stage 4 метрики, появились/исчезли обязательные ссылки, изменился fill_status, изменилась доступность или тип ссылки так, что это влияет на прохождение этапа.

3. Семантически-aware стратегия без тяжелого ML

Ввести второй, более богатый слой сравнения поверх текущего fingerprint, не заменяя его сразу.

Новая модель должна различать:





source_unchanged: текущий fingerprint совпал, можно сохранить прежнее быстрое поведение.



technical_change: изменились метаданные, но смысловые сигналы прежние.



artifact_change: изменился целевой файл или его revision/size/checksum, нужен Stage 4 или частичный пересчет.



link_structure_change: изменились обязательные ссылки, типы ссылок, strikethrough, доступность.



dissertation_metrics_change: изменились страницы, источники, compliance или причина skip.



status_change: изменились stopped_at, fill_status, phase statuses, passed/failed.



unknown_change: данных недостаточно, безопаснее выполнить текущий полный pipeline.

Минимальный comparison pipeline:





Нормализовать ссылки до стабильных ключей: Drive file id/folder id, canonical URL, link kind.



Сравнить дешевые метаданные Drive для report/dissertation/publication/LKB при доступности: file_id, mimeType, modifiedTime, size если доступен, md5Checksum если доступен для binary, headRevisionId если доступен и стабилен для данного типа файла.



Сравнить уже существующие snapshot-поля: stage3_extracted, phase statuses/warnings, metrics, sheet_enrichment_metrics, stage4_skipped_reason, fill_status.



Решить уровень действия: no-op, lightweight snapshot note, Stage 3 refresh, Stage 4 refresh, full current pipeline.

Важно: comparison должен быть advisory. Он объясняет причину изменения и предлагает действие, но текущий only_if_changed остается стабильным gate до тех пор, пока новая логика не будет покрыта тестами и включена флагом.

4. Какие данные безопасно сравнивать

Безопасные и дешевые метаданные:





Drive file/folder id, извлеченный из URL, потому что он стабильнее текстовой формы ссылки.



Canonical link kind: drive_file, drive_folder, обычный URL, empty.



mimeType, если уже получен в Stage 3 prefetch или легким Drive files.get.



modifiedTime как сигнал изменения, но не как доказательство содержательного изменения.



size и md5Checksum для binary-файлов, где Drive API их отдает.



headRevisionId/revision id только после проверки поведения на Google Docs/Drive типах; использовать как optional signal, не как обязательный инвариант.



Snapshot-level поля: phase.id/status, stage3_extracted.column_key/value/strikethrough, Stage 4 pages_total/sources_count/compliance, stage4_skipped_reason, fill_status, stopped_at.

Нежелательно считать meaning-сигналом само по себе:





generated_at snapshot.



Drive file name без других изменений.



Порядок файлов в папке.



Человеческий текст warnings без нормализованного кода причины.



Временную HTTP-недоступность без повторного подтверждения.

Полезные content-сигналы без ML-heavy подхода:





Хэш скачанного DOCX/PDF файла, если файл уже скачивается для Stage 4.



Извлеченные Stage 4 метрики: страницы, источники, compliance.



Легкая структурная сводка документа: количество параграфов/заголовков/таблиц для DOCX, если это уже доступно локальному парсеру.



Нормализованный заголовок диссертации и язык из enrichment, если они уже извлекаются.



Наличие/отсутствие обязательных разделов только если текущие парсеры уже умеют это дешево получать; не вводить LLM/embeddings/vector DB.

5. Как избежать дорогой переработки

Использовать каскад дешевых проверок:





Сначала текущий fingerprint и последняя история: ничего не менять в стабильном fast path.



Затем сравнение последнего snapshot с текущими легкими метаданными Drive, без скачивания больших файлов.



Скачивать диссертацию только если изменились file id/revision/size/checksum/modifiedTime или если предыдущие данные неполные.



Если файл уже скачан для Stage 4, вычислять content_hash и document_signature побочно, без второго IO.



Кешировать только как дополнительные поля в snapshot или в новой append-only истории, а не вводить новое хранилище.



Любой unknown трактовать как повод идти по текущему полному pipeline, чтобы не увеличивать false negatives.

6. Совместимый путь внедрения

Фаза A: наблюдаемость без влияния на поведение.





Добавить внутреннюю функцию сравнения последнего snapshot и текущих input/artifact metadata, но запускать ее в dry-run/log-only режиме.



Логировать категории изменений рядом с retry.row_check.*, не меняя решение unchanged.



Покрыть тестами false positive/false negative кейсы на уровне чистых функций.

Фаза B: additive metadata.





Добавить optional поля через совместимый контейнер, например artifact_fingerprints/change_summary, сохраняя чтение старых snapshot.



Не ломать schema_version=1 reader. Если контракт требует bump, сначала сделать reader, который читает v1 и v2, а затем писать v2 только после тестов обратной совместимости.



В историю не менять существующие колонки; для расширения использовать append-only дополнительные колонки в конце или отдельный optional snapshot payload, но не делать persistence rewrite.

Фаза C: объяснимые категории для пользователя/админа.





В справке и админском логе показывать не только changed/unchanged, а короткую причину: «ссылка на диссертацию прежняя, но Drive revision изменился», «изменились только метаданные отчета», «Stage 4 метрики изменились».



Не менять fill_status при technical-only/no-op изменениях.



Не создавать duplicate snapshots для чистого /справка, если итоговая категория source_unchanged или technical_noop.

Фаза D: управляемое включение.





Включать новую классификацию под env/config флагом.



Сначала использовать ее только для логов и admin diagnostics.



Затем разрешить частичный reprocess: Stage 3-only или Stage 4-only, но только когда это не нарушает текущую семантику RowCheckReport, history и snapshot.

7. Границы, которые не стоит пересекать

Явно не включать в ближайшую эволюцию:





ML-heavy redesign, embeddings, vector DB, LLM-сравнение текста диссертаций.



Переписывание хранения истории или snapshot persistence.



Массовую миграцию старых JSON на Drive.



Переименование snapshot-файлов или изменение project_snapshot_r{row}_*.json discovery.



Ломающее изменение only_if_changed: текущий короткий путь должен оставаться fallback и baseline.



Дублирование бизнес-логики в renderers: snapshot остается единственным источником структуры для справок и комиссионных артефактов.

8. Целевое состояние

Бот должен продолжать быстро отвечать «ничего не изменилось» там, где текущий fingerprint достаточен, но при наличии новых сигналов уметь объяснять тип изменения: ссылка, артефакт, метрики, статус или только техника. Это переводит retry из бинарного gate в объяснимую change intelligence, сохраняя существующий pipeline, Drive snapshots, history sheet и контракты рендеринга.