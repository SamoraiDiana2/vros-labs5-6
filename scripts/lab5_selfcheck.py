"""Самоперевірка ЛР5 (варіант 22): коректність хеш-таблиці С2, замків і формул стенду.

Кожна перевірка має очікуваний і фактичний результат; підсумок пишеться в
``results/lab5/checks.md`` і ``results/lab5/checks.json``. Код виходу 0 — усі
перевірки, що мають вердикт, пройдені.

Запуск: python -m scripts.lab5_selfcheck
"""

import argparse
import glob
import json
import math
import os
import random
import statistics
import sys
import threading
import time

from lab5 import bench
from lab5.rwlock import GlobalLock, RWLock
from lab5.structure import (
    DEFAULT_BUCKETS,
    HashNode,
    InvariantError,
    MAX_KEY,
    MIN_KEY,
    ResultCacheHashTable,
)

RESULTS = []


def check(name, expected, actual, ok):
    """ok=None — інформаційний пункт без вердикту."""
    RESULTS.append({"name": name, "expected": expected, "actual": actual, "ok": ok})
    mark = "ІНФО" if ok is None else ("OK" if ok else "ПОМИЛКА")
    print(f"[{mark}] {name}: {actual}", flush=True)


# ---------------------------------------------------------------- структура С2


def check_differential(ops=200_000, buckets=DEFAULT_BUCKETS, seed=22):
    """Хеш-таблиця С2 проти еталонної моделі на dict Python на ключах 0..10000."""
    rnd = random.Random(seed)
    cache = ResultCacheHashTable(buckets)
    model = {}
    mismatches = 0
    seq = 0

    for _ in range(ops):
        op = rnd.randrange(5)
        k = rnd.randint(MIN_KEY, MAX_KEY)
        if op in (0, 1):  # put(k, v)
            seq += 1
            v = (1, seq)
            exp = k not in model
            model[k] = v
            got = cache.put(k, v)
        elif op == 2:  # get(k)
            exp = model.get(k)
            got = cache.get(k)
        elif op == 3:  # contains(k)
            exp = k in model
            got = cache.contains(k)
        else:  # size()
            exp = len(model)
            got = cache.size()

        if exp != got or cache.size() != len(model):
            mismatches += 1

    inv = cache.check_invariants()
    all_keys_match = all(cache.get(k) == model.get(k) for k in range(MIN_KEY, MAX_KEY + 1))
    check(
        "Хеш-таблиця С2 збігається з еталонною моделлю на 200 000 операцій",
        "0 розбіжностей, усі ключі 0..10000 збігаються, інваріанти виконані",
        f"{mismatches} розбіжностей, ключі {'збігаються' if all_keys_match else 'НЕ збігаються'}, "
        f"розмір {inv['size']}, вставок {inv['inserted']}, оновлень {inv['updated']}",
        mismatches == 0 and all_keys_match and inv["ok"],
    )


def check_key_bounds():
    """Перевірка діапазону ключів 0..10000 та відхилення некоректних record_id."""
    cache = ResultCacheHashTable(DEFAULT_BUCKETS)
    cache.put(MIN_KEY, "first")
    cache.put(MAX_KEY, "last")
    edges_ok = (
        cache.get(MIN_KEY) == "first"
        and cache.get(MAX_KEY) == "last"
        and cache.size() == 2
    )

    rejected = 0
    for bad_key, exc_type in (
        (MIN_KEY - 1, ValueError),
        (MAX_KEY + 1, ValueError),
        (True, TypeError),
        ("42", TypeError),
    ):
        try:
            cache.put(bad_key, "bad")
        except exc_type:
            rejected += 1

    inv = cache.check_invariants()
    check(
        "Межі ключів record_id 0..10000",
        "крайні ключі 0 і 10000 приймаються, вихід за межі або не-int дають виняток",
        f"крайні ключі ok={edges_ok}, відхилено некоректних ключів {rejected} з 4, "
        f"розмір {inv['size']}",
        edges_ok and rejected == 4 and inv["ok"],
    )


def check_empty():
    cache = ResultCacheHashTable(DEFAULT_BUCKETS)
    res = (cache.get(0), cache.get(5000), cache.get(10000), cache.contains(42), cache.size())
    inv = cache.check_invariants()
    check(
        "Операції на порожній хеш-таблиці С2",
        "get повертає None, contains повертає False, size 0, інваріанти виконані",
        f"{res}, інваріанти ok={inv['ok']}",
        res == (None, None, None, False, 0) and inv["ok"],
    )


def check_invariant_detector():
    """Перевірка інваріантів має ловити кожне пошкодження хеш-таблиці С2."""

    def fresh():
        c = ResultCacheHashTable(16)
        for k in range(10):
            c.put(k, (1, k + 1))
        return c

    cases = {}

    # 1. Ключ у чужому бакеті
    c = fresh()
    src_b = next(i for i, h in enumerate(c._buckets) if h is not None)
    dst_b = (src_b + 1) % c.num_buckets
    node = c._buckets[src_b]
    c._buckets[src_b] = node.next
    node.next = c._buckets[dst_b]
    c._buckets[dst_b] = node
    cases["ключ у чужому бакеті"] = c

    # 2. Дублікат ключа
    c = fresh()
    b0 = c._bucket_index(0)
    c._buckets[b0] = HashNode(0, (1, 999), c._buckets[b0])
    c._size += 1
    c._inserted += 1
    cases["дублікат ключа"] = c

    # 3. Ключ поза межами 0..10000
    c = fresh()
    b1 = c._bucket_index(1)
    c._buckets[b1].key = 10001
    cases["ключ поза 0..10000"] = c

    # 4. Хибний лічильник _size
    c = fresh()
    c._size -= 1
    cases["хибний розмір _size"] = c

    # 5. Цикл у ланцюжку бакета
    c = fresh()
    b2 = c._bucket_index(2)
    c._buckets[b2].next = c._buckets[b2]
    cases["цикл у ланцюжку бакета"] = c

    caught = []
    for name, damaged in cases.items():
        try:
            damaged.check_invariants()
        except InvariantError:
            caught.append(name)

    check(
        "Перевірка інваріантів С2 ловить пошкодження",
        f"усі {len(cases)} пошкоджень дають InvariantError",
        f"спіймано {len(caught)} з {len(cases)}: {', '.join(caught)}",
        len(caught) == len(cases),
    )


# ---------------------------------------------------------------- замки (n = 12: 6 + 6)


def _exclusion_probe(lock, n_w, n_r, seconds, hold=0.0003):
    """Потоки тримають замок із time.sleep усередині (sleep відпускає GIL, тож
    перекриття, якщо замок його допускає, справді станеться) і рахують, хто всередині."""
    state = {"r": 0, "w": 0, "max_r": 0, "max_w": 0, "bad": 0, "ops_r": 0, "ops_w": 0}
    guard = threading.Lock()
    stop = [False]

    def writer():
        while not stop[0]:
            lock.acquire_write()
            with guard:
                state["w"] += 1
                if state["w"] > 1 or state["r"] > 0:
                    state["bad"] += 1
                state["max_w"] = max(state["max_w"], state["w"])
            time.sleep(hold)
            with guard:
                state["w"] -= 1
                state["ops_w"] += 1
            lock.release_write()

    def reader():
        while not stop[0]:
            lock.acquire_read()
            with guard:
                state["r"] += 1
                if state["w"] > 0:
                    state["bad"] += 1
                state["max_r"] = max(state["max_r"], state["r"])
            time.sleep(hold)
            with guard:
                state["r"] -= 1
                state["ops_r"] += 1
            lock.release_read()

    ths = (
        [threading.Thread(target=writer, daemon=True) for _ in range(n_w)]
        + [threading.Thread(target=reader, daemon=True) for _ in range(n_r)]
    )
    for t in ths:
        t.start()
    time.sleep(seconds)
    stop[0] = True
    for t in ths:
        t.join(10)
    state["alive"] = sum(t.is_alive() for t in ths)
    return state


def check_global_exclusion():
    s = _exclusion_probe(GlobalLock(), 6, 6, 1.5)
    inside = max(s["max_r"], s["max_w"])
    check(
        "Глобальний замок (n=12: 6+6): взаємне виключення",
        "усередині не більше одного потоку будь-якої ролі",
        f"найбільше одночасно: читачів {s['max_r']}, писців {s['max_w']}; порушень "
        f"{s['bad']}; операцій {s['ops_w']} + {s['ops_r']}",
        s["bad"] == 0 and inside == 1 and s["alive"] == 0,
    )


def check_rw_exclusion(policy):
    s = _exclusion_probe(RWLock(policy), 6, 6, 1.5)
    name = "перевага читача" if policy == "reader" else "перевага писця"
    check(
        f"RWLock ({name}, n=12: 6+6): писець виключний, читачі разом",
        "порушень 0; писців одночасно 1; читачів одночасно > 1",
        f"найбільше одночасно: читачів {s['max_r']}, писців {s['max_w']}; порушень "
        f"{s['bad']}; операцій запису {s['ops_w']}, читання {s['ops_r']}",
        s["bad"] == 0 and s["max_w"] == 1 and s["max_r"] > 1 and s["alive"] == 0,
    )
    return s


def check_policy_order(policy):
    """Читач R1 тримає замок; писець W стає в чергу; приходить читач R2.
    Перевага читача: R2 заходить раніше за W. Перевага писця: W раніше за R2."""
    lock = RWLock(policy)
    log = []
    lk = threading.Lock()

    def note(x):
        with lk:
            log.append(x)

    lock.acquire_read()  # R1

    def w():
        lock.acquire_write()
        note("W")
        time.sleep(0.02)
        lock.release_write()

    def r2():
        lock.acquire_read()
        note("R2")
        lock.release_read()

    tw = threading.Thread(target=w, daemon=True)
    tw.start()
    time.sleep(0.05)
    tr = threading.Thread(target=r2, daemon=True)
    tr.start()
    time.sleep(0.05)
    entered_while_r1 = list(log)
    lock.release_read()
    tw.join(5)
    tr.join(5)
    if policy == "reader":
        exp = ["R2", "W"]
        name = "перевага читача"
    else:
        exp = ["W", "R2"]
        name = "перевага писця"
    check(
        f"Політика RWLock ({name}): порядок входу",
        f"читач R1 тримає, писець W чекає, приходить читач R2 → {' → '.join(exp)}",
        f"поки тримав R1, увійшли: {entered_while_r1 or 'ніхто'}; порядок {' → '.join(log)}",
        log == exp,
    )


# ---------------------------------------------------------------- без замка


class NullLock:
    def acquire_read(self):
        pass

    def release_read(self):
        pass

    def acquire_write(self):
        pass

    def release_write(self):
        pass


def _switch_point():
    """Порожній виклик функції: точка, де CPython може перемкнути потік."""
    return None


class YieldingHashTable(ResultCacheHashTable):
    """Та сама хеш-таблиця С2, але між читанням голови бакета / перевіркою дубліката
    і записом нового вузла в бакет стоїть виклик функції для детермінованого прояву гонки."""

    __slots__ = ()

    def put(self, record_id, result):
        self._validate_key(record_id)
        idx = self._bucket_index(record_id)
        head = self._buckets[idx]
        node = head
        while node is not None:
            if node.key == record_id:
                node.value = result
                self._updated += 1
                return False
            node = node.next
        _switch_point()
        self._buckets[idx] = HashNode(record_id, result, head)
        self._size += 1
        self._inserted += 1
        if self._size > self._max_size:
            self._max_size = self._size
        return True


def _race_trial(ht_cls, lock, seconds):
    """6 писців (як у варіанті n=12) без пауз виконують put(k, v), потім перевірка інваріантів."""
    cache = ht_cls(DEFAULT_BUCKETS)
    stop = [False]

    def worker(tid):
        rnd = random.Random(2200 + tid)
        acq, rel = lock.acquire_write, lock.release_write
        seq = 0
        while not stop[0]:
            k = rnd.randint(MIN_KEY, MAX_KEY)
            seq += 1
            acq()
            cache.put(k, (tid, seq))
            rel()

    ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(6)]
    for t in ths:
        t.start()
    time.sleep(seconds)
    stop[0] = True
    for t in ths:
        t.join(10)
    try:
        cache.check_invariants()
        return None
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def check_races(seconds=2.0, tries=5):
    """Чи ловить стенд гонку в хеш-таблиці С2 і чи усувають її замки."""
    default = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    out = {}
    try:
        cases = [
            ("справжня С2, без замка", ResultCacheHashTable, NullLock),
            ("С2 із точкою перемикання, без замка", YieldingHashTable, NullLock),
            ("С2 із точкою перемикання, глобальний замок", YieldingHashTable, GlobalLock),
            ("С2 із точкою перемикання, RWLock читача", YieldingHashTable, lambda: RWLock("reader")),
            ("С2 із точкою перемикання, RWLock писця", YieldingHashTable, lambda: RWLock("writer")),
        ]
        for name, cls, mk in cases:
            out[name] = [_race_trial(cls, mk(), seconds) for _ in range(tries)]
    finally:
        sys.setswitchinterval(default)

    def broken(name):
        return [o for o in out[name] if o]

    real = broken("справжня С2, без замка")
    check(
        "Справжня хеш-таблиця С2 без замка, інтервал перемикання 1 мкс",
        "інформаційно: чи відтворюється гонка на цьому інтерпретаторі",
        f"порушено в {len(real)} з {tries} прогонів по {seconds:.0f} с",
        None,
    )
    inj = broken("С2 із точкою перемикання, без замка")
    check(
        "Перевірка інваріантів С2 бачить гонку",
        f"С2 з точкою перемикання без замка ламається хоча б в одному з {tries} прогонів",
        f"порушено в {len(inj)} з {tries}" + (f"; приклад: {inj[0]}" if inj else ""),
        len(inj) > 0,
    )
    locked = {k: len(broken(k)) for k in out if "замок" in k or "RWLock" in k}
    locked.pop("С2 із точкою перемикання, без замка", None)
    check(
        "Обидва замки усувають цю гонку в С2",
        f"0 порушень з {tries} для кожного замка",
        "; ".join(f"{k.split(', ', 1)[1]}: {v} з {tries}" for k, v in locked.items()),
        all(v == 0 for v in locked.values()),
    )
    return out


# ---------------------------------------------------------------- суміш операцій та інваріанти стенду


class CountingHashTable(ResultCacheHashTable):
    __slots__ = ("puts", "gets", "key_sum")

    def __init__(self, buckets):
        super().__init__(buckets)
        self.puts = 0
        self.gets = 0
        self.key_sum = 0

    def put(self, record_id, result):
        self.puts += 1
        self.key_sum += record_id
        return super().put(record_id, result)

    def get(self, record_id, default=None):
        self.gets += 1
        return super().get(record_id, default)


def check_op_mix(seconds=2.0):
    """Перевірка робочого навантаження варіанта 22 на n=12 потоках:
    6 непарних потоків виконують put(k, v), 6 парних — get(k) на ключах 0..10000,
    після чого перевіряються обидва інваріанти стенду."""
    cache = CountingHashTable(DEFAULT_BUCKETS)
    lock = GlobalLock()
    state = [bench.MEASURE]
    n = 12
    valid_writers = tuple(tid for tid in range(1, n + 1) if tid % 2 == 1)
    res = [{"thread": tid, "role": "write" if tid % 2 else "read"} for tid in range(1, n + 1)]
    ths = []
    for i, r in enumerate(res):
        tid = r["thread"]
        rnd = random.Random(2200 + tid)
        if tid % 2:
            ths.append(
                threading.Thread(
                    target=bench.writer_loop, args=(cache, lock, state, rnd, tid, r)
                )
            )
        else:
            ths.append(
                threading.Thread(
                    target=bench.reader_loop, args=(cache, lock, state, rnd, valid_writers, r)
                )
            )

    for th in ths:
        th.start()
    time.sleep(seconds)
    state[0] = bench.STOP
    for th in ths:
        th.join(10)

    writers = [r for r in res if r["role"] == "write"]
    readers = [r for r in res if r["role"] == "read"]
    inv = bench.verify_bench_invariants(cache, writers, readers)

    counted_w = sum(r["ops"] for r in writers)
    counted_r = sum(r["ops"] for r in readers)
    mean_key = cache.key_sum / cache.puts if cache.puts else 0.0
    expected_mean = (MIN_KEY + MAX_KEY) / 2.0
    # Стандартне відхилення середнього рівномірного розподілу на [0, 10000]
    sigma_mean = (MAX_KEY - MIN_KEY) / math.sqrt(12 * max(1, cache.puts))
    uniform_ok = abs(mean_key - expected_mean) < 5 * sigma_mean

    ok = (
        inv["ok"]
        and counted_w == cache.puts
        and counted_r == cache.gets
        and uniform_ok
    )
    check(
        "Суміш операцій та інваріанти стенду (n=12, put/get, ключі 0..10000)",
        "виклики put/get збігаються з лічильниками потоків; середній ключ ≈ 5000; "
        "розмір С2 = кількості різних записаних ключів",
        f"put: {cache.puts}/{counted_w}, get: {cache.gets}/{counted_r}; "
        f"середній ключ {mean_key:.1f} (очік. {expected_mean:.0f}); "
        f"ключів у таблиці {inv['size']} == різних записаних {inv['distinct_written_keys']}",
        ok,
    )


# ---------------------------------------------------------------- формули


def check_percentiles():
    data = [list(range(1, 101)), list(range(1, 201)), [5.0], list(range(1, 11))]
    got = []
    for d in data:
        arr = bench.array("d", d)
        p = bench.percentiles([arr])
        got.append((p["p50"], p["p99"]))
    exp = [(50.0, 99.0), (100.0, 198.0), (5.0, 5.0), (5.0, 10.0)]
    rnd = random.Random(3)
    vals = list(range(1, 1001))
    rnd.shuffle(vals)
    parts = [bench.array("d", vals[i::3]) for i in range(3)]
    p = bench.percentiles(parts)
    split_ok = (p["p50"], p["p99"]) == (500.0, 990.0)
    check(
        "Перцентиль p_q = τ_(⌈q·N⌉)",
        f"{exp} і (500, 990) на 1..1000, розбитому між трьома потоками",
        f"{got}; розбитий ряд: ({p['p50']:.0f}, {p['p99']:.0f})",
        got == exp and split_ok,
    )


def check_timer(calls=1_000_000):
    """Крок таймера і вартість одного виклику: обидва входять у виміряні t_cs і τ."""
    pc = time.perf_counter
    deltas = []
    for _ in range(200_000):
        a = pc()
        b = pc()
        deltas.append(b - a)
    steps = sorted(set(round(d * 1e9) for d in deltas if d > 0))[:3]
    costs = []
    for _ in range(5):
        t0 = pc()
        for _ in range(calls):
            pc()
        costs.append((pc() - t0) / calls * 1e9)
    cost = statistics.median(costs)
    info = time.get_clock_info("perf_counter")
    check(
        "Таймер perf_counter",
        "інформаційно: крок таймера і скільки один його виклик додає до t_cs",
        f"роздільність за системою {info.resolution * 1e9:.0f} нс; найменші ненульові "
        f"різниці {steps} нс; один виклик ≈ {cost:.0f} нс (медіана 5 серій по "
        f"{calls} викликів)",
        None,
    )
    return {"resolution_ns": info.resolution * 1e9, "steps_ns": steps, "call_ns": cost}


def check_recompute(out_dir):
    """Незалежний перерахунок метрик із сирих прогонів проти того, що записав стенд."""
    worst = 0.0
    n_checked = 0
    n_runs = 0
    problems = []
    for path in sorted(glob.glob(os.path.join(out_dir, "threads_*.json"))):
        for run in json.load(open(path, encoding="utf-8")):
            n_runs += 1
            d = run["duration_s"]
            for role, k in (("write", run["n_w"]), ("read", run["n_r"])):
                st = run[role]
                counts = [t["ops"] for t in run["per_thread"] if t["role"] == role]
                n_ops = sum(counts)
                mu = n_ops / k
                cv = math.sqrt(sum((c - mu) ** 2 for c in counts) / k) / mu
                pairs = [
                    (st["ops"], n_ops),
                    (st["X"], n_ops / d),
                    (st["x_per_thread"], n_ops / d / k),
                    (st["w"], st["wait_sum_s"] / (k * d)),
                    (st["cv"], cv),
                    (st["h"], st["p99_us"] / st["p50_us"]),
                ]
                for a, b in pairs:
                    rel = abs(a - b) / abs(b) if b else abs(a - b)
                    worst = max(worst, rel)
                    n_checked += 1
                    if rel > 1e-9:
                        problems.append((os.path.basename(path), role, a, b))
                if not 0 <= st["w"] <= 1:
                    problems.append((os.path.basename(path), role, "w поза 0..1", st["w"]))
            tot = run["total"]
            phi = run["write"]["X"] / (run["write"]["X"] + run["read"]["X"])
            for a, b in (
                (tot["X"], run["write"]["X"] + run["read"]["X"]),
                (tot["phi_w"], phi),
                (tot["phi_w"] + tot["phi_r"], 1.0),
            ):
                rel = abs(a - b) / abs(b)
                worst = max(worst, rel)
                n_checked += 1
                if rel > 1e-9:
                    problems.append((os.path.basename(path), "total", a, b))

    s = json.load(open(os.path.join(out_dir, "summary.json"), encoding="utf-8"))
    g, rw = s["global"], s["rw"]
    derived = {
        "S_n_global": g["total"]["X"] / s["x1_global"],
        "E_n_global": g["total"]["X"] / s["x1_global"] / g["n"],
        "S_n_rw": rw["total"]["X"] / s["x1_rw"],
        "E_n_rw": rw["total"]["X"] / s["x1_rw"] / rw["n"],
        "S_w": rw["write"]["X"] / g["write"]["X"],
        "S_r": rw["read"]["X"] / g["read"]["X"],
        "S_total": rw["total"]["X"] / g["total"]["X"],
        "rho": g["total"]["X"] * s["t_cs_bar_us"] / 1e6,
        "t_cs_bar_us": (g["n_w"] * s["t_cs_w_us"] + g["n_r"] * s["t_cs_r_us"]) / g["n"],
        "x_limit": 1e6 / s["t_cs_bar_us"],
    }
    for k, v in derived.items():
        rel = abs(s[k] - v) / abs(v)
        worst = max(worst, rel)
        n_checked += 1
        if rel > 1e-9:
            problems.append(("summary.json", k, s[k], v))
    check(
        "Незалежний перерахунок X, x, w, cv, h, φ, S, E, ρ, t̄_cs із сирих прогонів",
        "збіг із записаним стендом до 1e-9 відносної похибки, w у межах 0..1",
        f"перевірено {n_checked} величин у {n_runs} прогонах і summary.json; найбільша "
        f"відносна розбіжність {worst:.1e}; проблем {len(problems)}",
        not problems,
    )


def check_counters_vs_ops(out_dir):
    """Лічильники структури С2 (inserted + updated) охоплюють і прогрів;
    різниця з N_w — це операції put під час прогріву."""
    rows = []
    for path in ("threads_global.json", "threads_rw.json", "threads_rw-writer.json"):
        for run in json.load(open(os.path.join(out_dir, path), encoding="utf-8")):
            inv = run["invariants"]
            calls = inv["inserted"] + inv["updated"]
            rows.append((path, calls, run["write"]["ops"], calls - run["write"]["ops"]))
    ok = all(extra > 0 for *_, extra in rows)
    extras = [extra for *_, extra in rows]
    check(
        "Лічильники структури С2 (inserted + updated) проти N_w",
        "викликів put у структурі більше за N_w рівно на операції прогріву (> 0)",
        f"надлишок {min(extras)}–{max(extras)} викликів на {len(rows)} прогонах",
        ok,
    )
    return rows


# ---------------------------------------------------------------- звіт


def write_md(path, extra):
    lines = [
        "# Самоперевірка ЛР5 (варіант 22: С2, n = 12, ключі 0..10000)",
        "",
        "Згенеровано `python -m scripts.lab5_selfcheck`. ІНФО — пункт без вердикту.",
        "",
        "| # | Перевірка | Очікувано | Фактично | Вердикт |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(RESULTS, 1):
        v = "ІНФО" if r["ok"] is None else ("OK" if r["ok"] else "ПОМИЛКА")
        lines.append(f"| {i} | {r['name']} | {r['expected']} | {r['actual']} | {v} |")
    passed = sum(1 for r in RESULTS if r["ok"])
    total = sum(1 for r in RESULTS if r["ok"] is not None)
    lines += [
        "",
        f"**Пройдено {passed} з {total}**; інформаційних пунктів "
        f"{sum(1 for r in RESULTS if r['ok'] is None)}.",
        "",
    ]
    lines += extra
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Самоперевірка ЛР5 (варіант 22)")
    ap.add_argument("--out-dir", default="results/lab5")
    a = ap.parse_args(argv)

    check_differential()
    check_key_bounds()
    check_empty()
    check_invariant_detector()
    check_global_exclusion()
    rw_r = check_rw_exclusion("reader")
    rw_w = check_rw_exclusion("writer")
    check_policy_order("reader")
    check_policy_order("writer")
    check_op_mix()
    check_percentiles()
    check_recompute(a.out_dir)
    rows = check_counters_vs_ops(a.out_dir)
    timer = check_timer()
    races = check_races()

    extra = [
        "## Лічильники структури С2 і прогрів",
        "",
        "| Файл | Викликів put(k, v) у структурі | N_w за D | Різниця (прогрів) |",
        "|---|---|---|---|",
    ]
    for path, calls, nw, ex in rows:
        extra.append(f"| {path} | {calls} | {nw} | {ex} |")
    extra += [
        "",
        "## Гонка без замка і з замками",
        "",
        "Інтервал перемикання потоків 1 мкс, 6 писців (n=12) без пауз, 5 прогонів по 2 с.",
        "",
        "| Варіант | Прогонів з порушенням | Перше порушення |",
        "|---|---|---|",
    ]
    for name, outs in races.items():
        bad = [o for o in outs if o]
        extra.append(f"| {name} | {len(bad)} з {len(outs)} | {bad[0] if bad else '—'} |")
    write_md(os.path.join(a.out_dir, "checks.md"), extra)
    with open(os.path.join(a.out_dir, "checks.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "variant": 22,
                "structure": "С2",
                "n": 12,
                "checks": RESULTS,
                "timer": timer,
                "rw_reader_probe": rw_r,
                "rw_writer_probe": rw_w,
                "races": races,
                "counters": [
                    dict(zip(("file", "calls", "N_w", "extra"), r)) for r in rows
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    failed = [r for r in RESULTS if r["ok"] is False]
    print(
        f"\nпройдено {sum(1 for r in RESULTS if r['ok'])} з "
        f"{sum(1 for r in RESULTS if r['ok'] is not None)}; "
        f"звіт: {os.path.join(a.out_dir, 'checks.md')}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
