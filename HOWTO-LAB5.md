# ЛР5 докладно: що де лежить і як перевірити кожне число

Короткий запуск — [RUN-LAB5.md](RUN-LAB5.md). Звіт — [REPORT-LAB5.md](REPORT-LAB5.md).
Варіант 19: структура С2 (хеш-таблиця), n = 12, місткість 10000.

## Файли

| Шлях | Що там |
|---|---|
| [lab5/structure.py](lab5/structure.py) | хеш-таблиця кешу результатів, без будь-якої синхронізації; перевірка інваріантів і повний знімок стану |
| [lab5/rwlock.py](lab5/rwlock.py) | `RWLock` на `threading.Condition` з двома політиками і обгортка `GlobalLock` над `threading.Lock` |
| [lab5/bench.py](lab5/bench.py) | стенд: гарячі цикли писця і читача, режими `threads`, `single`, `cs`, перцентилі, зведення по ролях |
| [scripts/lab5_run.py](scripts/lab5_run.py) | оркестратор: три прогони на конфігурацію, вибір медіанного прогону, таблиці 1-6 |
| [scripts/lab5_readonly_check.py](scripts/lab5_readonly_check.py) | доказ, що операції читання не змінюють структуру |
| [scripts/lab5_switch.py](scripts/lab5_switch.py) | дослід про хвіст p99 і інтервал перемикання потоків |
| [scripts/lab5_selfcheck.py](scripts/lab5_selfcheck.py) | самоперевірка: еталонна модель хеш-таблиці, інваріанти, обидва замки, гонка без замка, частки операцій, перерахунок метрик |
| [scripts/lab5_charts.py](scripts/lab5_charts.py) | три діаграми за `summary.json` |

## Сирі дані

| Файл у `results/lab5/` | Що в ньому |
|---|---|
| `cs_runs.json` | три прогони режиму `cs`: t_cs,w, t_cs,r, t̄_cs, межа 1 / t̄_cs |
| `single_global.json`, `single_rw.json` | три прогони X_1 під кожним замком |
| `threads_global_random.json` | крок 2, три прогони |
| `threads_rw_random.json` | крок 3, три прогони (перевага читача) |
| `threads_rw-writer_random.json` | додатковий прогін з перевагою писця |
| `threads_global_pair.json`, `threads_rw_pair.json` | контрольна суміш запису |
| `readonly_check.json` | результат перевірки читальних операцій |
| `switch_interval.json` | дослід про хвіст тривалості |
| `checks.md`, `checks.json` | протокол самоперевірки: очікуване, фактичне, вердикт |
| `summary.json` | усі числа звіту в одному файлі: медіанні прогони і метрики кроку 4 |
| `tables.md` | таблиці 1-6 плюс розкид, інваріанти і додаткові прогони |

Кожен файл прогону `threads` містить: `duration_s` (фактичне D), `write` і `read` зі
зведенням по ролі, `total`, `per_thread` з кількістю операцій кожного потоку,
`invariants` і `host`. У `host.conditions` — умови саме цього прогону, зняті перед
стартом потоків: `ac_power`, `battery_percent`, `battery_saver`, `power_scheme_guid`,
`timer_resolution_ms` (поточна роздільність системного таймера Windows) і
`background_cpu_percent` (завантаження процесора за секунду до старту, без стенду).

`results/lab5/v1/` — перша серія, знята тим самим стендом без запису умов; її
порівняння з основною — у розділі «Відтворюваність» звіту.
