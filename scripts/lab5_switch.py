"""Додатковий дослід ЛР5 (варіант 22): звідки беруться хвости p99 під глобальним замком.

Під глобальним замком завантаженість самого замка rho невелика, тобто замок вільний
більшість часу, а потоки все одно звітують високе w. Отже t_wait міряє не час, поки замок
тримає хтось інший, а час, поки потік не отримав процесор: у CPython це інтервал
перемикання потоків (``sys.setswitchinterval``, за замовчуванням 5 мс) разом з
квантом планувальника Windows.

Дослід: конфігурація кроку 2 для хеш-таблиці С2 (n = 12, ключі 0..10000) на кількох
значеннях інтервалу перемикання, по три прогони на кожне. Якби хвіст тривалості
створював інтервал перемикання потоків CPython, p99 падало б разом з ним.

Запуск: python -m scripts.lab5_switch --out results/lab5/switch_interval.json
"""

import argparse
import json
import os
import statistics
import sys
import time

from lab5 import bench
from lab5.structure import DEFAULT_BUCKETS, MAX_KEY, MIN_KEY
from scripts.lab5_run import wait_quiet

INTERVALS = (0.005, 0.001, 0.0002)
DEFAULT_N = 12


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="p99 проти інтервалу перемикання потоків (варіант 22: С2, n=12)"
    )
    ap.add_argument("--out", default="results/lab5/switch_interval.json")
    ap.add_argument("--lock", default="global", choices=("global", "rw", "rw-writer"))
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--buckets", type=int, default=DEFAULT_BUCKETS)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--warmup", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=190519)
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args(argv)

    default = sys.getswitchinterval()
    rows = []
    try:
        for iv in INTERVALS:
            sys.setswitchinterval(iv)
            print(f"інтервал перемикання {iv * 1000:.1f} мс", flush=True)
            reps = []
            for i in range(a.repeats):
                sys.setswitchinterval(default)
                wait_quiet(f"інтервал {iv * 1000:.1f} мс, прогін {i + 1}")
                sys.setswitchinterval(iv)
                r = bench.run_threads(
                    a.lock, a.n, a.buckets, a.warmup, a.duration, a.seed
                )
                reps.append(
                    {
                        "X": r["total"]["X"],
                        "X_w": r["write"]["X"],
                        "X_r": r["read"]["X"],
                        "p50_us": r["total"]["p50_us"],
                        "p99_us": r["total"]["p99_us"],
                        "p99_w_us": r["write"]["p99_us"],
                        "p99_r_us": r["read"]["p99_us"],
                        "w_w": r["write"]["w"],
                        "w_r": r["read"]["w"],
                        "wait_max_ms": r["total"]["wait_max_ms"],
                        "cv": r["total"]["cv"],
                        "invariants_ok": r["invariants"]["ok"],
                        "conditions": r["host"].get("conditions"),
                    }
                )
                print(
                    f"  прогін {i + 1}: X = {r['total']['X']:.0f} оп/с, "
                    f"p50 = {r['total']['p50_us']:.1f} мкс, "
                    f"p99 = {r['total']['p99_us']:.1f} мкс",
                    flush=True,
                )
                time.sleep(0.5)
            med = {
                k: statistics.median([x[k] for x in reps])
                for k in reps[0]
                if k not in ("conditions", "invariants_ok")
            }
            rows.append(
                {
                    "switch_interval_ms": iv * 1000,
                    "all_invariants_ok": all(x["invariants_ok"] for x in reps),
                    "runs": reps,
                    "median": med,
                }
            )
            print(
                f"  медіана: X = {med['X']:.0f} оп/с, p50 = {med['p50_us']:.1f} мкс, "
                f"p99 = {med['p99_us']:.1f} мкс"
            )
    finally:
        sys.setswitchinterval(default)

    out = {
        "variant": {
            "variant": 22,
            "structure": "С2",
            "n": a.n,
            "buckets": a.buckets,
            "key_range": [MIN_KEY, MAX_KEY],
        },
        "lock": a.lock,
        "n": a.n,
        "buckets": a.buckets,
        "default_switch_interval_ms": default * 1000,
        "duration_s": a.duration,
        "rows": rows,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"підсумок: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
