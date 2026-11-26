# Запуск ЛР6 (варіант 22: Д4 OrganCMNIST, Ш1, P_max = 3, B = 192, K_c = 20, s_f = 50)

Докер, кластер і база з ЛР1–ЛР4 тут не потрібні: робота виконується процесами Python на
одному вузлі, бекенд обміну gloo через loopback. Потрібен Python 3.10+ і збірка PyTorch
для CPU. Усі команди запускаються з кореня репозиторію.

Перед серією: ноутбук від мережі, режим економії заряду вимкнений, Docker Desktop і
кластер k3d зупинені, інші програми по змозі закриті. Прогони міряють час кроку в
мілісекундах, тож перед кожним прогоном оркестратор сам перевіряє живлення і чекає, доки
фонове завантаження процесора впаде до 10 %, і записує умови в файл прогону.

## 0. Залежності і датасет

```powershell
pip install torch numpy scikit-learn
```

Датасет варіанта — OrganCMNIST (ліцензія CC BY 4.0). Пакет `medmnist` не потрібен,
файл беремо з офіційного архіву MedMNIST v3.0 на Zenodo:

```powershell
New-Item -ItemType Directory -Force data | Out-Null; Invoke-WebRequest -Uri "https://zenodo.org/records/10519652/files/organcmnist.npz?download=1" -OutFile data/organcmnist.npz
```

Той самий файл кладе й пакет `medmnist`:

```powershell
pip install medmnist; python -c "from medmnist import OrganCMNSIT; OrganCMNSIT(split='train', download=True, root='data')"
```

Папка `data/` не входить у git (датасет 19 МБ і ліцензія CC BY).

## 1. Уся серія і таблиці 1-6 за один раз

```powershell
python -m scripts.lab6_run --out-dir results/lab6
```

Знімає все, що вимагають кроки 1-5 умови: сім конфігурацій вимірювання швидкості по
девʼять прогонів (`--repeats`) і по одному прогону на кожен режим навчання — прогони
навчання детерміновані, бо зерно і перестановки семплера фіксовані.

| Прогони | Для чого |
|---|---|
| `stand` | таблиця 1: процесор, ядра, n_params, M, n_th |
| `train` в одному процесі без `torch.distributed` | крок 1: ACC і AUC одного процесу |
| `grad-single` і `equiv` | крок 2: max \|g − ḡ\| / max \|g\| і суми ваг після 50 кроків |
| `train` з DDP на P_max | крок 2: ACC, AUC і втрата на кожному кроці (основа порівняння кроку 5) |
| `speed` сімох конфігурацій: один процес без `torch.distributed`; P_max з ручною синхронізацією, з DDP і з `no_sync`; P = 1 з DDP і з `no_sync` при B = const; P = 1 з DDP при b = const | кроки 1-4, рядки таблиць 2, 3 і 4 |
| `train` з P = 1 батчем b і `train` на P_max з eta_P | крок 4, таблиця 5 |
| `train` з контрольними точками і відмовою на кроці s_f | крок 5, таблиця 6 |

Прогони швидкості знімаються **по колу**: прогін 1 кожної конфігурації, потім прогін 2 і
так далі. На ноутбуці розкид часу кроку між прогонами доходить до 40 %, і коли всі
прогони однієї конфігурації йдуть поспіль, повільний проміжок машини дістається цілій
конфігурації — різниця t_step − t_step,ns (обмін DDP) через це виходила навіть
відʼємною. По колу такий проміжок однаково зачіпає всі режими, і t_comm,exp рахується як
медіана різниці по колах.

Разом близько 50-60 хвилин, з яких більшість — очікування тихого фону перед прогонами
(до 60 с на прогін). Пише `results/lab6/*.json`,
[results/lab6/tables.md](results/lab6/tables.md) з таблицями 1-6 і
`results/lab6/summary.json`.

Корисні ключі: `--steps speed` знімає лише швидкість (а `1,2,4,5` — лише навчання і
відмову), `--repeats 3` скорочує кількість кіл, `--max-bg 15` піднімає поріг фонового
завантаження, `--allow-battery` дозволяє серію на батареї (тоді числа з основною серією
не порівнювати), `--tables-only` перебудовує таблиці з уже знятих файлів:

```powershell
python -m scripts.lab6_run --tables-only --out-dir results/lab6
```

## 2. Самоперевірка

```powershell
python -m scripts.lab6_selfcheck --out-dir results/lab6
```

Близько хвилини, 50 перевірок. Числа таблиць звіряються з формулами умови і з
незалежними реалізаціями: n_params, нормування і вміст датасету, n_e, розбиття індексів
`DistributedSampler`, рівність ḡ = g у подвійній точності, ACC і AUC проти власної
реалізації macro-OVR через U-статистику, X проти 50 * B / t, S, E, e, S_w, E_w проти
формул, збіг втрат прогону з відмовою і без неї, узгодженість контрольної точки. Окремий
блок (15 перевірок) звіряє самі прогони з параметрами умови: n_th і OMP_NUM_THREADS,
режими кроків 1, 2, 4 і 5 (sync, P, b, B, eta, епохи), типовий розмір кошика DDP, команда
прогону з відмовою, 10 + 50 кроків у кожному прогоні швидкості, живлення від мережі.
Пише `results/lab6/checks.md` і `checks.json`; код виходу 0 — усі перевірки пройдені.

## 3. Діаграми

```powershell
python -m scripts.lab6_charts --out-dir results/lab6/charts
```

Графік X(P) з ідеальною прямою P * X(1) (вимога кроку 3), S_w(P), склад часу кроку і
втрата на кроці в прогоні з відмовою.

## Окремі прогони руками

Один процес без `torch.distributed` — навчання і вимірювання швидкості:

```powershell
python -m lab6.train --task train --sync none --global-batch 192 --epochs 3
```

```powershell
python -m lab6.train --task speed --sync none --global-batch 192
```

Група процесів запускається через обгортку `scripts.lab6_torchrun` — це той самий
`torch.distributed.run`, що й `torchrun`, з обходом збірки PyTorch для Windows без libuv
(деталі в [lab6/winuv.py](lab6/winuv.py)). На Linux або в збірці з libuv замість неї
працює звичайний `torchrun` з тими самими ключами.

```powershell
python -m scripts.lab6_torchrun --standalone --nproc_per_node=3 -m lab6.train --task speed --sync ddp --global-batch 192
```

```powershell
python -m scripts.lab6_torchrun --standalone --nproc_per_node=3 -m lab6.train --task speed --sync manual --global-batch 192
```

Перевірка еквівалентності градієнта (спершу один процес пише g у файл):

```powershell
python -m lab6.train --task grad-single --sync none --global-batch 192 --grad-file results/lab6/grad_single.pt
```

```powershell
python -m scripts.lab6_torchrun --standalone --nproc_per_node=3 -m lab6.train --task equiv --sync manual --global-batch 192 --grad-file results/lab6/grad_single.pt
```

Навчання з контрольними точками і відмовою на кроці 50, зі статичною адресою групи і
одним перезапуском:

```powershell
python -m scripts.lab6_torchrun --nnodes=1 --nproc_per_node=3 --max-restarts=1 --rdzv-backend=c10d --rdzv-endpoint=127.0.0.1:29500 --rdzv-id=lab6 -m lab6.train --task train --sync ddp --global-batch 192 --epochs 3 --ckpt-dir results/lab6/s5_failure --ckpt-interval 20 --fail-step 50
```

Ключі `lab6.train`: `--task` (`stand`, `speed`, `grad-single`, `equiv`, `train`),
`--sync` (`none`, `manual`, `ddp`, `ddp-nosync`), `--global-batch` або
`--per-proc-batch`, `--lr`, `--epochs`, `--threads` (n_th, типово ⌊C_phys / P_max⌋),
`--ckpt-dir`, `--ckpt-interval`, `--fail-step`, `--out`, `--tag`.