"""Крок 3 ЛР5 (варіант 22): перевірка незмінності стану структури С2 при читанні.

Перевірка складається з двох рівнів:

1. Статичний аналіз AST. Інспектуються методи читання ``get``, ``contains``, ``size``
   (а також чисті внутрішні методи ``_validate_key`` і ``_bucket_index``, до яких вони
   звертаються) класу ``ResultCacheHashTable``: фіксуються будь-які спроби присвоєння
   в атрибути або індекси, інструкції ``del`` чи виклики сторонніх методів.

2. Динамічна перевірка за знімком (``fingerprint``). Стан хеш-таблиці (кількість
   елементів, структура бакетів та ідентифікатори всіх вузлів у ланцюжках) фіксується
   до і після серії читань як наявних, так і відсутніх ключів: спершу в одному потоці,
   потім паралельно у ``n_r = 6`` потоках (половина від n = 12) під ``RWLock``.

Запуск: python -m scripts.lab5_readonly_check
"""

import argparse
import ast
import inspect
import json
import random
import sys
import threading
import time

from lab5 import structure
from lab5.rwlock import RWLock
from lab5.structure import (
    DEFAULT_BUCKETS,
    MAX_KEY,
    MIN_KEY,
    ResultCacheHashTable,
)

TARGET_CLASS = "ResultCacheHashTable"
READ_METHODS = ("get", "contains", "size")
INTERNAL_PURE_HELPERS = ("_validate_key", "_bucket_index")
INSPECTED_METHODS = READ_METHODS + INTERNAL_PURE_HELPERS
ALLOWED_CALLS = set(INSPECTED_METHODS)


class ReadOnlyAstInspector(ast.NodeVisitor):
    """AST-інспектор для виявлення мутацій стану всередині методів читання."""

    def __init__(self, method_name):
        self.method_name = method_name
        self.violations = []

    def _check_target(self, target):
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            self.violations.append(
                f"{self.method_name}: зміна поля або елемента ({ast.dump(target)[:48]})"
            )

    def visit_Assign(self, node):
        for target in node.targets:
            self._check_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_Delete(self, node):
        self.violations.append(f"{self.method_name}: інструкція del")
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr not in ALLOWED_CALLS:
            self.violations.append(f"{self.method_name}: виклик .{func.attr}()")
        self.generic_visit(node)


def run_static_check():
    """Статичний аналіз AST читальних і допоміжних методів ResultCacheHashTable."""
    source = inspect.getsource(structure)
    tree = ast.parse(source)

    cls_node = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == TARGET_CLASS),
        None,
    )
    if cls_node is None:
        return {
            "checked": [],
            "findings": [f"клас {TARGET_CLASS} не знайдено у модулі structure"],
            "ok": False,
        }

    methods_map = {
        fn.name: fn for fn in cls_node.body if isinstance(fn, ast.FunctionDef)
    }
    findings = []
    checked = []

    for name in INSPECTED_METHODS:
        fn_node = methods_map.get(name)
        if fn_node is None:
            findings.append(f"у класі {TARGET_CLASS} відсутній метод {name}")
            continue
        checked.append(name)
        inspector = ReadOnlyAstInspector(name)
        inspector.visit(fn_node)
        findings.extend(inspector.violations)

    return {
        "class": TARGET_CLASS,
        "checked": checked,
        "findings": findings,
        "ok": len(findings) == 0,
    }


def _populate_cache(seed=22, step=2):
    """Створює хеш-таблицю С2 та заповнює половину діапазону ключів 0..10000."""
    cache = ResultCacheHashTable(DEFAULT_BUCKETS)
    rnd = random.Random(seed)
    keys = list(range(MIN_KEY, MAX_KEY + 1, step))
    rnd.shuffle(keys)
    for seq, k in enumerate(keys, start=1):
        cache.put(k, (1, seq))
    return cache


def _run_read_step(cache, op_idx, key):
    mod = op_idx % 3
    if mod == 0:
        return cache.get(key)
    if mod == 1:
        return cache.contains(key)
    return cache.size()


def run_dynamic_single(ops=200_000):
    """Однопотокова перевірка незмінності fingerprint при читанні."""
    cache = _populate_cache()
    before = cache.fingerprint()
    span = MAX_KEY - MIN_KEY + 1

    for i in range(ops):
        key = MIN_KEY + ((i * 37) % span)
        _run_read_step(cache, i, key)

    after = cache.fingerprint()
    return {
        "ops": ops,
        "size": cache.size(),
        "unchanged": before == after,
        "invariants_ok": cache.check_invariants()["ok"],
    }


def run_dynamic_threads(n_r=6, seconds=2.0):
    """Багатопотокова перевірка (n_r=6 читачів для варіанту n=12) під RWLock."""
    cache = _populate_cache()
    before = cache.fingerprint()
    lock = RWLock("reader")
    stop_flag = [False]
    counts = [0] * n_r

    def reader_worker(worker_idx):
        rnd = random.Random(1000 + worker_idx)
        randrange = rnd.randrange
        lo, hi = MIN_KEY, MAX_KEY + 1
        local_ops = 0
        while not stop_flag[0]:
            k = randrange(lo, hi)
            lock.acquire_read()
            _run_read_step(cache, local_ops, k)
            lock.release_read()
            local_ops += 1
        counts[worker_idx] = local_ops

    threads = [
        threading.Thread(target=reader_worker, args=(i,), name=f"ro-{i}", daemon=True)
        for i in range(n_r)
    ]
    for th in threads:
        th.start()
    time.sleep(seconds)
    stop_flag[0] = True
    for th in threads:
        th.join(30)

    after = cache.fingerprint()
    return {
        "threads": n_r,
        "ops": sum(counts),
        "ops_per_thread": counts,
        "size": cache.size(),
        "unchanged": before == after,
        "invariants_ok": cache.check_invariants()["ok"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Перевірка незмінності стану С2 при операціях читання (варіант 22)"
    )
    ap.add_argument("--out", help="шлях до JSON-файлу зі звітом перевірки")
    ap.add_argument("--nr", type=int, default=6, help="кількість потоків читання (за замовч. 6)")
    ap.add_argument("--ops", type=int, default=200_000, help="кількість читань в одному потоці")
    ap.add_argument("--seconds", type=float, default=2.0, help="тривалість багатопотокового тесту, с")
    args = ap.parse_args(argv)

    st = run_static_check()
    ds = run_dynamic_single(args.ops)
    dt = run_dynamic_threads(args.nr, args.seconds)

    ok = (
        st["ok"]
        and ds["unchanged"]
        and ds["invariants_ok"]
        and dt["unchanged"]
        and dt["invariants_ok"]
    )

    print(
        "Статична перевірка читальних методів С2:",
        "зміни стану не знайдено" if st["ok"] else "ЗНАЙДЕНО ЗМІНИ",
    )
    print(f"  перевірено методів: {', '.join(st['checked'])}")
    for item in st["findings"]:
        print(f"  !! {item}")

    print(
        f"Один потік, {ds['ops']} читань: знімок структури "
        f"{'не змінився' if ds['unchanged'] else 'ЗМІНИВСЯ'}, "
        f"інваріанти ok={ds['invariants_ok']}"
    )
    print(
        f"{dt['threads']} потоків під RWLock, {dt['ops']} читань: знімок структури "
        f"{'не змінився' if dt['unchanged'] else 'ЗМІНИВСЯ'}, "
        f"інваріанти ok={dt['invariants_ok']}"
    )
    print("ПІДСУМОК:", "читання не змінює структуру С2" if ok else "ПЕРЕВІРКА НЕ ПРОЙДЕНА")

    result = {
        "variant": 22,
        "structure": "С2",
        "ok": ok,
        "static": st,
        "dynamic_single": ds,
        "dynamic_threads": dt,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"підсумок: {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
