"""Структура С2 варіанта 22: хеш-таблиця кешу результатів за record_id з ключами від 0 до 10000.

Синхронізації тут немає зовсім: замок накладає стенд ([lab5/bench.py]), щоб той самий
код структури працював і під глобальним замком, і під RWLock. Вбудовані dict/set не
використовуються — таблиця побудована на власному масиві бакетів та однозвʼязних списках.
"""

MIN_KEY = 0
MAX_KEY = 10000
DEFAULT_BUCKETS = 1024  # степінь двійки для швидкого індексування через бітову маску


class HashNode:
    """Вузол ланцюжка колізій у бакеті хеш-таблиці. ``__slots__`` — без словника атрибутів."""

    __slots__ = ("key", "value", "next")

    def __init__(self, key, value, nxt=None):
        self.key = key
        self.value = value
        self.next = nxt


class InvariantError(AssertionError):
    pass


class ResultCacheHashTable:
    """Хеш-таблиця кешу результатів за record_id (0..10000) методом ланцюжків.

    Операції запису: ``put``, ``remove``, ``clear``.
    Операції читання: ``get``, ``contains``, ``size`` — не змінюють жодного поля
    ні таблиці, ні вузлів (перевіряється [scripts/lab5_readonly_check.py]).
    """

    __slots__ = (
        "num_buckets",
        "_mask",
        "_buckets",
        "_size",
        "_inserted",
        "_updated",
        "_removed",
        "_misses",
        "_max_size",
    )

    def __init__(self, num_buckets=DEFAULT_BUCKETS):
        if num_buckets < 1 or (num_buckets & (num_buckets - 1)) != 0:
            raise ValueError("кількість бакетів має бути додатним степенем двійки")
        self.num_buckets = num_buckets
        self._mask = num_buckets - 1
        self._buckets = [None] * num_buckets
        self._size = 0

        # лічильники для перевірки інваріантів після прогону
        self._inserted = 0
        self._updated = 0
        self._removed = 0
        self._misses = 0
        self._max_size = 0

    @staticmethod
    def _validate_key(record_id):
        if not isinstance(record_id, int) or isinstance(record_id, bool):
            raise TypeError("record_id має бути цілим числом")
        if not (MIN_KEY <= record_id <= MAX_KEY):
            raise ValueError(f"record_id={record_id} поза діапазоном {MIN_KEY}..{MAX_KEY}")

    def _bucket_index(self, record_id):
        # Мультиплікативне хешування (Кнут) для рівномірного розподілу ключів 0..10000
        return ((record_id * 2654435761) >> 16) & self._mask

    # ---------- запис ----------

    def put(self, record_id, result):
        """Додає новий запис у кеш або оновлює результат за існуючим record_id.
        Повертає True, якщо створено новий запис, і False, якщо оновлено існуючий."""
        self._validate_key(record_id)
        idx = self._bucket_index(record_id)
        node = self._buckets[idx]

        while node is not None:
            if node.key == record_id:
                node.value = result
                self._updated += 1
                return False
            node = node.next

        self._buckets[idx] = HashNode(record_id, result, self._buckets[idx])
        self._size += 1
        self._inserted += 1
        if self._size > self._max_size:
            self._max_size = self._size
        return True

    def remove(self, record_id):
        """Видаляє запис за record_id і повертає збережене значення,
        або None, якщо ключа не було в кеші."""
        self._validate_key(record_id)
        idx = self._bucket_index(record_id)
        prev = None
        node = self._buckets[idx]

        while node is not None:
            if node.key == record_id:
                if prev is None:
                    self._buckets[idx] = node.next
                else:
                    prev.next = node.next
                node.next = None
                self._size -= 1
                self._removed += 1
                return node.value
            prev = node
            node = node.next

        self._misses += 1
        return None

    def clear(self):
        """Очищає всю таблицю кешу."""
        removed_now = self._size
        for i in range(self.num_buckets):
            self._buckets[i] = None
        self._size = 0
        self._removed += removed_now

    # ---------- читання ----------

    def get(self, record_id, default=None):
        """Повертає кешований результат за record_id або default, якщо ключ відсутній."""
        self._validate_key(record_id)
        idx = self._bucket_index(record_id)
        node = self._buckets[idx]
        while node is not None:
            if node.key == record_id:
                return node.value
            node = node.next
        return default

    def contains(self, record_id):
        """Перевіряє наявність record_id у кеші."""
        self._validate_key(record_id)
        idx = self._bucket_index(record_id)
        node = self._buckets[idx]
        while node is not None:
            if node.key == record_id:
                return True
            node = node.next
        return False

    def size(self):
        return self._size

    # ---------- перевірка інваріантів ----------

    def check_invariants(self):
        """Інваріанти С2 з умови:
        - усі ключі в межах 0..10000 і не дублюються;
        - кожен вузол лежить у бакеті, що відповідає його хешу;
        - немає циклів у ланцюжках колізій;
        - фактична кількість вузлів збігається з _size та різницею _inserted - _removed.
        """
        max_possible = MAX_KEY - MIN_KEY + 1
        if not 0 <= self._size <= max_possible:
            raise InvariantError(f"розмір {self._size} поза межами 0..{max_possible}")

        total_nodes = 0
        non_empty_buckets = 0
        max_chain_len = 0
        seen_keys = bytearray(max_possible)

        for bucket_idx in range(self.num_buckets):
            node = self._buckets[bucket_idx]
            if node is not None:
                non_empty_buckets += 1

            chain_len = 0
            while node is not None:
                chain_len += 1
                total_nodes += 1
                if total_nodes > max_possible:
                    raise InvariantError("кількість вузлів перевищує діапазон ключів: цикл у ланцюжку")

                key = node.key
                if not isinstance(key, int) or not (MIN_KEY <= key <= MAX_KEY):
                    raise InvariantError(f"некоректний ключ {key} у бакеті {bucket_idx}")

                expected_idx = self._bucket_index(key)
                if expected_idx != bucket_idx:
                    raise InvariantError(
                        f"ключ {key} у бакеті {bucket_idx}, а має бути у {expected_idx}"
                    )

                if seen_keys[key]:
                    raise InvariantError(f"дублікат ключа record_id={key} у таблиці")
                seen_keys[key] = 1

                node = node.next

            if chain_len > max_chain_len:
                max_chain_len = chain_len

        if total_nodes != self._size:
            raise InvariantError(
                f"знайдено вузлів {total_nodes}, а лічильник розміру _size={self._size}"
            )

        if self._inserted - self._removed != self._size:
            raise InvariantError(
                f"вставлено {self._inserted}, видалено {self._removed}, а розмір {self._size}"
            )

        return {
            "ok": True,
            "size": self._size,
            "max_size": self._max_size,
            "num_buckets": self.num_buckets,
            "non_empty_buckets": non_empty_buckets,
            "max_chain_len": max_chain_len,
            "inserted": self._inserted,
            "updated": self._updated,
            "removed": self._removed,
            "remove_misses": self._misses,
        }

    def fingerprint(self):
        """Повний знімок стану структури: розмір та всі бакети разом із посиланнями на вузли.
        Використовується, щоб довести, що операції читання нічого не змінюють."""
        buckets_snapshot = []
        for head in self._buckets:
            chain = []
            node = head
            while node is not None:
                chain.append((id(node), node.key, node.value, id(node.next)))
                node = node.next
            buckets_snapshot.append(tuple(chain))
        return (self._size, tuple(buckets_snapshot))
