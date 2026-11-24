"""Стенд ЛР5 (варіант 22): n=12 потоків над хеш-таблицею С2 під глобальним замком або під RWLock.

Потоки з непарними номерами виконують лише запис put(k, v), з парними — лише читання get(k).
Ключі record_id вибираються з діапазону 0..10000. Операції йдуть одна за одною без пауз,
аргументи генеруються в потоці до виклику операції.
Для кожної операції записуються тривалість tau = t_ret - t_call і час очікування замка
t_wait = t_acq - t_call; t_call береться до захоплення замка, t_ret — після звільнення.

Режими запуску:
  threads   n потоків (за замовчуванням n = 12)
  single    X_1: один потік по черзі виконує записи і читання в пропорції n_w : n_r
  cs        t_cs,w і t_cs,r: час утримання замка без конкуренції
"""

import argparse
import json
import math
import os
import platform
import random
import sys
import threading
import time
from array import array
from fractions import Fraction

from lab5.rwlock import make_lock
from lab5.structure import (
    DEFAULT_BUCKETS,
    InvariantError,
    MAX_KEY,
    MIN_KEY,
    ResultCacheHashTable,
)

WARMUP = 0
MEASURE = 1
STOP = 2

DEFAULT_N = 12
KEY_SPACE = MAX_KEY - MIN_KEY + 1
PERCENTILES = (0.5, 0.99)


# ---------------------------------------------------------------- гарячі цикли


def writer_loop(cache, lock, state, rnd, tid, result):
    """Операція запису С2: put(k, v) для випадкового k з 0..10000."""
    tau = array("d")
    wait = array("d")
    tau_add = tau.append
    wait_add = wait.append
    pc = time.perf_counter
    acquire = lock.acquire_write
    release = lock.release_write
    put = cache.put
    randrange = rnd.randrange
    lo = MIN_KEY
    hi = MAX_KEY + 1

    written_mask = bytearray(KEY_SPACE)
    ops = 0
    seq = 0
    while True:
        st = state[0]
        if st == STOP:
            break
        # --- аргументи операції готуються тут, до виклику ---
        k = randrange(lo, hi)
        seq += 1
        v = (tid, seq)
        # --- сама операція ---
        t_call = pc()
        acquire()
        t_acq = pc()
        put(k, v)
        release()
        t_ret = pc()

        written_mask[k - lo] = 1
        if st == MEASURE:
            tau_add(t_ret - t_call)
            wait_add(t_acq - t_call)
            ops += 1

    result["ops"] = ops
    result["tau"] = tau
    result["wait"] = wait
    result["written_mask"] = written_mask
    result["max_seq"] = seq


def reader_loop(cache, lock, state, rnd, valid_writers, result):
    """Операція читання С2: get(k) для випадкового k з 0..10000."""
    tau = array("d")
    wait = array("d")
    tau_add = tau.append
    wait_add = wait.append
    pc = time.perf_counter
    acquire = lock.acquire_read
    release = lock.release_read
    get = cache.get
    randrange = rnd.randrange
    lo = MIN_KEY
    hi = MAX_KEY + 1

    ops = 0
    bad_read = None
    while True:
        st = state[0]
        if st == STOP:
            break
        # --- аргументи операції готуються тут, до виклику ---
        k = randrange(lo, hi)
        # --- сама операція ---
        t_call = pc()
        acquire()
        t_acq = pc()
        val = get(k)
        release()
        t_ret = pc()

        if val is not None and bad_read is None:
            if (
                not isinstance(val, tuple)
                or len(val) != 2
                or val[0] not in valid_writers
                or not isinstance(val[1], int)
                or val[1] < 1
            ):
                bad_read = (k, val)

        if st == MEASURE:
            tau_add(t_ret - t_call)
            wait_add(t_acq - t_call)
            ops += 1

    result["ops"] = ops
    result["tau"] = tau
    result["wait"] = wait
    result["bad_read"] = bad_read


def single_loop(cache, lock, state, rnd, n_w, n_r, result):
    """X_1: один потік по черзі виконує n_w записів put(k, v) і n_r читань get(k)."""
    tau_w = array("d")
    wait_w = array("d")
    tau_r = array("d")
    wait_r = array("d")
    pc = time.perf_counter
    acq_w, rel_w = lock.acquire_write, lock.release_write
    acq_r, rel_r = lock.acquire_read, lock.release_read
    put = cache.put
    get = cache.get
    randrange = rnd.randrange
    lo = MIN_KEY
    hi = MAX_KEY + 1

    written_mask = bytearray(KEY_SPACE)
    ops_w = ops_r = 0
    seq = 0
    bad_read = None
    while True:
        st = state[0]
        if st == STOP:
            break
        for _ in range(n_w):
            k = randrange(lo, hi)
            seq += 1
            v = (1, seq)
            t_call = pc()
            acq_w()
            t_acq = pc()
            put(k, v)
            rel_w()
            t_ret = pc()
            written_mask[k - lo] = 1
            if st == MEASURE:
                tau_w.append(t_ret - t_call)
                wait_w.append(t_acq - t_call)
                ops_w += 1
        for _ in range(n_r):
            k = randrange(lo, hi)
            t_call = pc()
            acq_r()
            t_acq = pc()
            val = get(k)
            rel_r()
            t_ret = pc()
            if val is not None and bad_read is None:
                if not isinstance(val, tuple) or len(val) != 2 or val[0] != 1 or not (1 <= val[1] <= seq):
                    bad_read = (k, val)
            if st == MEASURE:
                tau_r.append(t_ret - t_call)
                wait_r.append(t_acq - t_call)
                ops_r += 1

    result["writer"] = {
        "thread": 1,
        "ops": ops_w,
        "tau": tau_w,
        "wait": wait_w,
        "written_mask": written_mask,
        "max_seq": seq,
    }
    result["reader"] = {
        "thread": 2,
        "ops": ops_r,
        "tau": tau_r,
        "wait": wait_r,
        "bad_read": bad_read,
    }


def cs_loop(cache, lock, state, n_w, n_r, result):
    """t_cs: час від отримання до звільнення замка, один потік, без конкуренції.

    t_cs = t_rel - t_acq, де t_acq узятий одразу після захоплення, а t_rel — останньою
    дією перед звільненням, тобто сам виклик release у час утримання не входить.
    """
    cs_w = array("d")
    cs_r = array("d")
    pc = time.perf_counter
    acq_w, rel_w = lock.acquire_write, lock.release_write
    acq_r, rel_r = lock.acquire_read, lock.release_read
    put = cache.put
    get = cache.get
    rnd = random.Random(20250519)
    randrange = rnd.randrange
    lo = MIN_KEY
    hi = MAX_KEY + 1

    written_mask = bytearray(KEY_SPACE)
    seq = 0
    bad_read = None
    while True:
        st = state[0]
        if st == STOP:
            break
        for _ in range(n_w):
            k = randrange(lo, hi)
            seq += 1
            v = (1, seq)
            acq_w()
            t_acq = pc()
            put(k, v)
            t_rel = pc()
            rel_w()
            written_mask[k - lo] = 1
            if st == MEASURE:
                cs_w.append(t_rel - t_acq)
        for _ in range(n_r):
            k = randrange(lo, hi)
            acq_r()
            t_acq = pc()
            val = get(k)
            t_rel = pc()
            rel_r()
            if val is not None and bad_read is None:
                if not isinstance(val, tuple) or len(val) != 2 or val[0] != 1 or not (1 <= val[1] <= seq):
                    bad_read = (k, val)
            if st == MEASURE:
                cs_r.append(t_rel - t_acq)

    result["cs_w"] = cs_w
    result["cs_r"] = cs_r
    result["writer"] = {"thread": 1, "written_mask": written_mask, "max_seq": seq}
    result["reader"] = {"thread": 2, "bad_read": bad_read}


# ---------------------------------------------------------------- перевірка інваріантів стенду


def verify_bench_invariants(cache, writers, readers):
    """Перевіряє специфічні інваріанти варіанта 22 для стенду С2:
    1. Усі внутрішні інваріанти структури С2 (check_invariants).
    2. Кожне ненульове значення, прочитане get(k) під час роботи або після зупинки,
       належить одному з потоків запису і відповідає ключу, який справді записувався.
    3. Кількість ключів у таблиці дорівнює кількості різних ключів, записаних хоча б раз.
    """
    struct_inv = cache.check_invariants()

    for r in readers:
        if r.get("bad_read") is not None:
            k, val = r["bad_read"]
            raise InvariantError(f"читач прочитав некоректне значення {val!r} для record_id={k}")

    max_seq_by_tid = {w["thread"]: w["max_seq"] for w in writers}
    union_mask = bytearray(KEY_SPACE)
    for w in writers:
        wmask = w["written_mask"]
        for i in range(KEY_SPACE):
            if wmask[i]:
                union_mask[i] = 1

    distinct_written_keys = sum(union_mask)

    for idx in range(KEY_SPACE):
        k = MIN_KEY + idx
        val = cache.get(k)
        was_written = bool(union_mask[idx])
        if not was_written:
            if val is not None:
                raise InvariantError(
                    f"ключ record_id={k} жодного разу не записувався, але get({k}) повернув {val!r}"
                )
        else:
            if (
                not isinstance(val, tuple)
                or len(val) != 2
                or val[0] not in max_seq_by_tid
                or not (1 <= val[1] <= max_seq_by_tid[val[0]])
            ):
                raise InvariantError(
                    f"get({k}) повернув {val!r}, що не є жодним із записаних для ключа значень"
                )
            if not writers[0]["written_mask"][idx] if len(writers) == 1 else False:
                raise InvariantError(f"невідповідність маски запису для record_id={k}")

            # Додаткова перевірка: чи саме цей потік-власник значення справді записував ключ k
            owner_tid = val[0]
            owner_wrote_k = any(
                w["written_mask"][idx] for w in writers if w["thread"] == owner_tid
            )
            if not owner_wrote_k:
                raise InvariantError(
                    f"get({k}) повернув значення від потоку {owner_tid}, який не записував ключ {k}"
                )

    actual_size = cache.size()
    if actual_size != distinct_written_keys:
        raise InvariantError(
            f"кількість ключів у таблиці ({actual_size}) не дорівнює кількості "
            f"різних ключів, записаних хоча б раз ({distinct_written_keys})"
        )

    struct_inv["distinct_written_keys"] = distinct_written_keys
    struct_inv["key_range"] = f"{MIN_KEY}..{MAX_KEY}"
    return struct_inv


# ---------------------------------------------------------------- статистика


def _sorted_values(arrays):
    """Злити масиви тривалостей потоків однієї ролі в один відсортований список."""
    total = sum(len(a) for a in arrays)
    if total == 0:
        return None
    merged = array("d")
    for a in arrays:
        merged.extend(a)
    return sorted(merged)


def order_index(q, n):
    """⌈q·N⌉ точно, у цілих числах: q береться як десятковий дріб (0,99 = 99/100),
    а не як двійкове наближення float."""
    frac = Fraction(str(q))
    k = -((-frac.numerator * n) // frac.denominator)
    return max(1, min(n, k))


def percentiles(arrays):
    """p_q = tau_(ceil(q*N)) — порядкова статистика, нумерація з одиниці."""
    data = _sorted_values(arrays)
    if data is None:
        return {f"p{int(q * 100)}": None for q in PERCENTILES}
    n = len(data)
    out = {}
    for q in PERCENTILES:
        out[f"p{int(q * 100)}"] = float(data[order_index(q, n) - 1])
    return out


def role_stats(per_thread, n_role, duration):
    """Зведення по ролі: X, x, перцентилі, w, cv, найбільший t_wait."""
    counts = [t["ops"] for t in per_thread]
    taus = [t["tau"] for t in per_thread]
    waits = [t["wait"] for t in per_thread]
    n_ops = sum(counts)
    wait_sum = sum(float(sum(w)) for w in waits)
    wait_max = max((max(w) for w in waits if len(w)), default=0.0)
    pct = percentiles(taus)
    k = len(counts)
    mu = n_ops / k if k else 0.0
    sigma = math.sqrt(sum((c - mu) ** 2 for c in counts) / k) if k else 0.0
    x = n_ops / duration
    p50, p99 = pct["p50"], pct["p99"]
    return {
        "threads": n_role,
        "ops": n_ops,
        "ops_per_thread": counts,
        "X": x,
        "x_per_thread": x / n_role if n_role else 0.0,
        "p50_us": p50 * 1e6 if p50 is not None else None,
        "p99_us": p99 * 1e6 if p99 is not None else None,
        "h": (p99 / p50) if p50 else None,
        "wait_sum_s": wait_sum,
        "wait_max_ms": wait_max * 1e3,
        "w": wait_sum / (n_role * duration) if n_role else 0.0,
        "mu": mu,
        "sigma": sigma,
        "cv": sigma / mu if mu else 0.0,
    }


def mean(a):
    return float(sum(a)) / len(a) if len(a) else 0.0


# ---------------------------------------------------------------- прогони


def _windows_conditions(sample_s=1.0):
    """Умови прогону на Windows: живлення, роздільність системного таймера і
    завантаження процесора іншими програмами перед стартом потоків стенду.
    """
    import ctypes
    from ctypes import wintypes

    out = {}

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wintypes.DWORD),
                    ("BatteryFullLifeTime", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32")
    ps = SYSTEM_POWER_STATUS()
    if k32.GetSystemPowerStatus(ctypes.byref(ps)):
        out["ac_power"] = {0: False, 1: True}.get(ps.ACLineStatus)
        out["battery_percent"] = None if ps.BatteryLifePercent == 255 else ps.BatteryLifePercent
        out["battery_saver"] = bool(ps.SystemStatusFlag & 1)

    try:
        ntdll = ctypes.WinDLL("ntdll")
        lo, hi, cur = wintypes.ULONG(), wintypes.ULONG(), wintypes.ULONG()
        ntdll.NtQueryTimerResolution(ctypes.byref(lo), ctypes.byref(hi), ctypes.byref(cur))
        out["timer_resolution_ms"] = cur.value / 1e4
    except OSError:
        pass

    try:
        import re
        import subprocess
        txt = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True,
                             timeout=5).stdout.decode("ascii", "replace")
        m = re.search(r"[0-9a-fA-F-]{36}", txt)
        out["power_scheme_guid"] = m.group(0) if m else None
    except (OSError, subprocess.SubprocessError):
        pass

    def times():
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))

        def v(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        return v(idle), v(kern), v(user)

    i0, k0, u0 = times()
    time.sleep(sample_s)
    i1, k1, u1 = times()
    total = (k1 - k0) + (u1 - u0)
    out["background_cpu_percent"] = (
        round(100.0 * (total - (i1 - i0)) / total, 1) if total else None)
    return out


def host_info(conditions=None):
    info = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "logical_cores": os.cpu_count(),
    }
    if conditions:
        info["conditions"] = conditions
    return info


def run_conditions():
    if sys.platform != "win32":
        return {}
    try:
        return _windows_conditions()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def run_threads(lock_kind, n, buckets, warmup, duration, seed):
    cond = run_conditions()
    n_w = (n + 1) // 2
    n_r = n // 2
    valid_writers = tuple(tid for tid in range(1, n + 1) if tid % 2 == 1)
    cache = ResultCacheHashTable(buckets)
    lock = make_lock(lock_kind)
    state = [WARMUP]
    results = []
    threads = []
    for tid in range(1, n + 1):
        res = {"thread": tid, "role": "write" if tid % 2 else "read"}
        results.append(res)
        rnd = random.Random(seed + tid)
        if tid % 2:
            target, args = writer_loop, (cache, lock, state, rnd, tid, res)
        else:
            target, args = reader_loop, (cache, lock, state, rnd, valid_writers, res)
        th = threading.Thread(target=target, args=args, name=f"t{tid}", daemon=True)
        threads.append(th)
    for th in threads:
        th.start()
    time.sleep(warmup)
    state[0] = MEASURE
    t0 = time.perf_counter()
    time.sleep(duration)
    t1 = time.perf_counter()
    state[0] = STOP
    for th in threads:
        th.join(60)
    d = t1 - t0

    writers = [r for r in results if r["role"] == "write"]
    readers = [r for r in results if r["role"] == "read"]
    inv = verify_bench_invariants(cache, writers, readers)

    w = role_stats(writers, n_w, d)
    r = role_stats(readers, n_r, d)
    total_ops = w["ops"] + r["ops"]
    x_total = total_ops / d
    both = percentiles([x["tau"] for x in results])
    counts = [x["ops"] for x in results]
    mu = sum(counts) / len(counts)
    sg = math.sqrt(sum((c - mu) ** 2 for c in counts) / len(counts))
    wait_sum = w["wait_sum_s"] + r["wait_sum_s"]
    return {
        "mode": "threads",
        "lock": lock_kind,
        "n": n,
        "n_w": n_w,
        "n_r": n_r,
        "buckets": buckets,
        "key_range": [MIN_KEY, MAX_KEY],
        "warmup_s": warmup,
        "duration_s": d,
        "seed": seed,
        "write": w,
        "read": r,
        "total": {
            "ops": total_ops,
            "X": x_total,
            "phi_w": w["X"] / x_total if x_total else 0.0,
            "phi_r": r["X"] / x_total if x_total else 0.0,
            "p50_us": both["p50"] * 1e6 if both["p50"] is not None else None,
            "p99_us": both["p99"] * 1e6 if both["p99"] is not None else None,
            "wait_max_ms": max(w["wait_max_ms"], r["wait_max_ms"]),
            "w": wait_sum / (n * d),
            "cv": sg / mu if mu else 0.0,
        },
        "invariants": inv,
        "per_thread": [
            {"thread": x["thread"], "role": x["role"], "ops": x["ops"]} for x in results
        ],
        "host": host_info(cond),
    }


def run_single(lock_kind, n, buckets, warmup, duration, seed):
    cond = run_conditions()
    n_w = (n + 1) // 2
    n_r = n // 2
    cache = ResultCacheHashTable(buckets)
    lock = make_lock(lock_kind)
    state = [WARMUP]
    res = {}
    rnd = random.Random(seed)
    th = threading.Thread(
        target=single_loop, args=(cache, lock, state, rnd, n_w, n_r, res),
        daemon=True)
    th.start()
    time.sleep(warmup)
    state[0] = MEASURE
    t0 = time.perf_counter()
    time.sleep(duration)
    t1 = time.perf_counter()
    state[0] = STOP
    th.join(60)
    d = t1 - t0

    inv = verify_bench_invariants(cache, [res["writer"]], [res["reader"]])
    w = role_stats([res["writer"]], 1, d)
    r = role_stats([res["reader"]], 1, d)
    total = w["ops"] + r["ops"]
    return {
        "mode": "single",
        "lock": lock_kind,
        "n": n,
        "ratio": f"{n_w}:{n_r}",
        "buckets": buckets,
        "key_range": [MIN_KEY, MAX_KEY],
        "duration_s": d,
        "write": w,
        "read": r,
        "total": {"ops": total, "X1": total / d},
        "invariants": inv,
        "host": host_info(cond),
    }


def run_cs(lock_kind, n, buckets, warmup, duration):
    cond = run_conditions()
    n_w = (n + 1) // 2
    n_r = n // 2
    cache = ResultCacheHashTable(buckets)
    lock = make_lock(lock_kind)
    state = [WARMUP]
    res = {}
    th = threading.Thread(
        target=cs_loop, args=(cache, lock, state, n_w, n_r, res), daemon=True)
    th.start()
    time.sleep(warmup)
    state[0] = MEASURE
    time.sleep(duration)
    state[0] = STOP
    th.join(60)

    inv = verify_bench_invariants(cache, [res["writer"]], [res["reader"]])
    t_cs_w = mean(res["cs_w"])
    t_cs_r = mean(res["cs_r"])
    t_bar = (n_w * t_cs_w + n_r * t_cs_r) / n
    return {
        "mode": "cs",
        "lock": lock_kind,
        "n": n,
        "n_w": n_w,
        "n_r": n_r,
        "buckets": buckets,
        "key_range": [MIN_KEY, MAX_KEY],
        "samples_w": len(res["cs_w"]),
        "samples_r": len(res["cs_r"]),
        "t_cs_w_us": t_cs_w * 1e6,
        "t_cs_r_us": t_cs_r * 1e6,
        "t_cs_bar_us": t_bar * 1e6,
        "x_limit": 1.0 / t_bar if t_bar else None,
        "invariants": inv,
        "host": host_info(cond),
    }


def print_summary(out):
    """Друк підсумків стенду: операції кожного потоку, X_w, X_r, p50 і p99 кожної
    ролі, найбільший t_wait, w_w і w_r, інваріанти С2."""
    m = out["mode"]
    print(f"режим {m}, замок {out['lock']}, n={out['n']}, D = {out.get('duration_s', 0):.3f} с")
    if m == "cs":
        print(f"  t_cs,w = {out['t_cs_w_us']:.3f} мкс ({out['samples_w']} операцій)")
        print(f"  t_cs,r = {out['t_cs_r_us']:.3f} мкс ({out['samples_r']} операцій)")
        print(f"  t_cs середній = {out['t_cs_bar_us']:.3f} мкс, межа 1/t_cs = "
              f"{out['x_limit']:.0f} оп/с")
    elif m == "single":
        print(f"  X_1 = {out['total']['X1']:.0f} оп/с у пропорції {out['ratio']} "
              f"({out['total']['ops']} операцій)")
    else:
        for t in out["per_thread"]:
            role = "запис" if t["role"] == "write" else "читання"
            print(f"  потік {t['thread']:>2} {role:<8} {t['ops']:>9} операцій")
        w, r, tot = out["write"], out["read"], out["total"]
        print(f"  X_w = {w['X']:.0f} оп/с, X_r = {r['X']:.0f} оп/с, X = {tot['X']:.0f} оп/с")
        print(f"  запис:   p50 {w['p50_us']:.2f} мкс, p99 {w['p99_us']:.2f} мкс, "
              f"w_w = {w['w']:.4f}, cv = {w['cv']:.4f}")
        print(f"  читання: p50 {r['p50_us']:.2f} мкс, p99 {r['p99_us']:.2f} мкс, "
              f"w_r = {r['w']:.4f}, cv = {r['cv']:.4f}")
        print(f"  найбільший t_wait запису = {w['wait_max_ms']:.3f} мс, "
              f"читання = {r['wait_max_ms']:.3f} мс")
        print(f"  phi_w = {tot['phi_w']:.4f}, phi_r = {tot['phi_r']:.4f}")
    inv = out["invariants"]
    print(f"  інваріанти: ok={inv['ok']}, ключів у таблиці {inv['size']} "
          f"(різних записано {inv['distinct_written_keys']}), "
          f"вставок {inv['inserted']}, оновлень {inv['updated']}, "
          f"непорожніх бакетів {inv['non_empty_buckets']}/{inv['num_buckets']}, "
          f"макс. ланцюжок {inv['max_chain_len']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Стенд ЛР5 (варіант 22): хеш-таблиця С2 під двома замками")
    ap.add_argument("mode", choices=("threads", "single", "cs"))
    ap.add_argument("--lock", default="global", choices=("global", "rw", "rw-writer"))
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="кількість потоків стенду (за замовч. 12)")
    ap.add_argument("--buckets", type=int, default=DEFAULT_BUCKETS, help="кількість бакетів (степінь 2)")
    ap.add_argument("--warmup", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=190519)
    ap.add_argument("--out", help="файл JSON для підсумку")
    a = ap.parse_args(argv)

    if a.mode == "threads":
        out = run_threads(a.lock, a.n, a.buckets, a.warmup, a.duration, a.seed)
    elif a.mode == "single":
        out = run_single(a.lock, a.n, a.buckets, a.warmup, a.duration, a.seed)
    else:
        out = run_cs(a.lock, a.n, a.buckets, a.warmup, a.duration)

    print_summary(out)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"підсумок: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
