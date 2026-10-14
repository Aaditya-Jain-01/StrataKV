from __future__ import annotations
import random
from typing import Iterator, List, Optional, Tuple

TOMBSTONE = "__STRATAKV_TOMBSTONE_VAL__"


class SkipListNode:
    __slots__ = ("key", "value", "forward")

    def __init__(self, key: str, value: str, level: int) -> None:
        self.key = key
        self.value = value
        self.forward: List[Optional[SkipListNode]] = [None] * level


class SkipListMemTable:
    def __init__(self, max_level: int = 16, p: float = 0.5) -> None:
        if not isinstance(max_level, int) or max_level < 1 or max_level > 64:
            raise ValueError(f"max_level must be an integer in [1, 64], got {max_level}")
        if not (0.0 < p < 1.0):
            raise ValueError(f"probability p must be strictly in (0.0, 1.0), got {p}")

        self.max_level = max_level
        self.p = p
        self.header = SkipListNode("", "", max_level)
        self.level = 1
        self.entry_count = 0
        self.size_bytes = 0

    def _random_level(self) -> int:
        lvl = 1
        while random.random() < self.p and lvl < self.max_level:
            lvl += 1
        return lvl

    def put(self, key: str, value: str) -> None:
        if not isinstance(key, str) or len(key) == 0:
            raise ValueError("Key must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError("Value must be a string")

        update: List[Optional[SkipListNode]] = [None] * self.max_level
        curr: SkipListNode = self.header

        for i in range(self.level - 1, -1, -1):
            while curr.forward[i] and curr.forward[i].key < key:
                curr = curr.forward[i]
            update[i] = curr

        target = curr.forward[0]

        if target and target.key == key:
            old_val_size = len(target.value.encode("utf-8"))
            new_val_size = len(value.encode("utf-8"))
            self.size_bytes += (new_val_size - old_val_size)
            target.value = value
            return

        new_level = self._random_level()
        if new_level > self.level:
            for i in range(self.level, new_level):
                update[i] = self.header
            self.level = new_level

        new_node = SkipListNode(key, value, new_level)
        for i in range(new_level):
            new_node.forward[i] = update[i].forward[i]
            update[i].forward[i] = new_node

        self.entry_count += 1
        self.size_bytes += len(key.encode("utf-8")) + len(value.encode("utf-8"))

    def get(self, key: str) -> Optional[str]:
        if not isinstance(key, str) or len(key) == 0:
            return None

        curr: SkipListNode = self.header
        for i in range(self.level - 1, -1, -1):
            while curr.forward[i] and curr.forward[i].key < key:
                curr = curr.forward[i]

        curr = curr.forward[0]
        if curr and curr.key == key:
            return curr.value
        return None

    def iter_items(self) -> Iterator[Tuple[str, str]]:
        curr = self.header.forward[0]
        while curr:
            yield curr.key, curr.value
            curr = curr.forward[0]

    def clear(self) -> None:
        self.header = SkipListNode("", "", self.max_level)
        self.level = 1
        self.entry_count = 0
        self.size_bytes = 0