"""Примітиви синхронізації для стенду ЛР5: RWLock та GlobalLock.

Використовуються виключно базові примітиви модуля ``threading`` (мʼютекс та умовна
змінна) без готових потокобезпечних колекцій.

Реалізовано дві стратегії керування доступом для ``RWLock``:
- ``"reader"`` (пріоритет читачів): читач отримує доступ одразу за відсутності
  активного писця; писець очікує звільнення замка всіма активними читачами.
- ``"writer"`` (пріоритет писців): поява писця в черзі очікування блокує вхід
  нових читачів до завершення роботи всіх писців, що очікують.
"""

import threading
from contextlib import contextmanager


class BaseSyncLock:
    """Базовий інтерфейс замків для стенду bench.py."""

    __slots__ = ()

    def acquire_read(self):
        raise NotImplementedError

    def release_read(self):
        raise NotImplementedError

    def acquire_write(self):
        raise NotImplementedError

    def release_write(self):
        raise NotImplementedError

    @contextmanager
    def read_guard(self):
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write_guard(self):
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()


class RWLock(BaseSyncLock):
    """Замок читання-запису на основі ``threading.Condition``."""

    __slots__ = (
        "policy",
        "_mutex",
        "_state_changed",
        "_active_readers",
        "_writer_active",
        "_writers_queued",
        "_can_read",
    )

    def __init__(self, policy="reader"):
        if policy not in ("reader", "writer"):
            raise ValueError(f"непідтримувана політика RWLock: {policy!r}")

        self.policy = policy
        self._mutex = threading.Lock()
        self._state_changed = threading.Condition(self._mutex)

        self._active_readers = 0
        self._writer_active = False
        self._writers_queued = 0

        # Вибір предиката доступу читача один раз при створенні замка
        if policy == "writer":
            self._can_read = self._can_read_writer_priority
        else:
            self._can_read = self._can_read_reader_priority

    # ---------- внутрішні предикати стану ----------

    def _can_read_reader_priority(self):
        return not self._writer_active

    def _can_read_writer_priority(self):
        return not self._writer_active and self._writers_queued == 0

    def _can_write(self):
        return not self._writer_active and self._active_readers == 0

    # ---------- операції читання ----------

    def acquire_read(self):
        with self._state_changed:
            while not self._can_read():
                self._state_changed.wait()
            self._active_readers += 1

    def release_read(self):
        with self._state_changed:
            if self._active_readers <= 0:
                raise RuntimeError("спроба звільнити замок читання, який не утримується")
            self._active_readers -= 1
            if self._active_readers == 0:
                self._state_changed.notify_all()

    # ---------- операції запису ----------

    def acquire_write(self):
        with self._state_changed:
            self._writers_queued += 1
            try:
                while not self._can_write():
                    self._state_changed.wait()
                self._writer_active = True
            finally:
                self._writers_queued -= 1

    def release_write(self):
        with self._state_changed:
            if not self._writer_active:
                raise RuntimeError("спроба звільнити замок запису, який не утримується")
            self._writer_active = False
            self._state_changed.notify_all()


class GlobalLock(BaseSyncLock):
    """Ексклюзивний замок (глобальний мʼютекс) для обох типів операцій."""

    __slots__ = ("_mutex", "acquire_read", "release_read", "acquire_write", "release_write")

    def __init__(self):
        self._mutex = threading.Lock()
        # Пряме зв'язування методів для мінімізації накладних витрат на виклик
        self.acquire_read = self._mutex.acquire
        self.release_read = self._mutex.release
        self.acquire_write = self._mutex.acquire
        self.release_write = self._mutex.release


_LOCK_BUILDERS = {
    "global": GlobalLock,
    "rw": lambda: RWLock("reader"),
    "rw-writer": lambda: RWLock("writer"),
}


def make_lock(kind):
    """Фабрика створення примітиву синхронізації за назвою режиму."""
    builder = _LOCK_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"невідомий спосіб синхронізації: {kind!r}")
    return builder()
