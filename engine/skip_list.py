from __future__ import annotations
import random
from typing import Iterator, List, Optional, Tuple

TOMBSTONE: str = "__STRATAKV_TOMBSTONE__"

class SkipListNode:
    __slots__ = ("key", "value", "forward")

    def __init__(self, key: str, value: str, level: int) -> None:
        self.key: str = key
        self.value: str = value
        self.forward: List[Optional[SkipListNode]] = [None] * level

class SkipListMemTable:
    def __init__(self, max_level: int = 16, p: float = 0.5) -> None:
        self.max_level: int = max_level
        self.p: float = p
        self.level: int = 1
        self.header: SkipListNode = SkipListNode(key="", value="", level=max_level)
        self.size_bytes: int = 0
        self.entry_count: int = 0

    def _random_level(self) -> int:
        lvl = 1
        while random.random() < self.p and lvl < self.max_level:
            lvl += 1
        return lvl

    def put(self, key: str, value: str) -> None:
        update: List[Optional[SkipListNode]] = [None] * self.max_level
        current: SkipListNode = self.header

        for i in range(self.level - 1, -1, -1):
            while current.forward[i] and current.forward[i].key < key:
                current = current.forward[i]
            update[i] = current

        current = current.forward[0]

        if current and current.key == key:
            old_val_bytes = len(current.value.encode("utf-8"))
            new_val_bytes = len(value.encode("utf-8"))
            self.size_bytes += (new_val_bytes - old_val_bytes)
            current.value = value
            return

        new_level = self._random_level()
        if new_level > self.level:
            for i in range(self.level, new_level):
                update[i] = self.header
            self.level = new_level

        new_node = SkipListNode(key=key, value=value, level=new_level)
        for i in range(new_level):
            new_node.forward[i] = update[i].forward[i]
            update[i].forward[i] = new_node

        key_bytes = len(key.encode("utf-8"))
        val_bytes = len(value.encode("utf-8"))
        pointer_overhead = new_level * 8
        self.size_bytes += (key_bytes + val_bytes + pointer_overhead)
        self.entry_count += 1

    def get(self, key: str) -> Optional[str]:
        current: SkipListNode = self.header
        for i in range(self.level - 1, -1, -1):
            while current.forward[i] and current.forward[i].key < key:
                current = current.forward[i]

        current = current.forward[0]
        if current and current.key == key:
            return current.value
        return None

    def delete(self, key: str) -> None:
        self.put(key, TOMBSTONE)

    def iter_items(self) -> Iterator[Tuple[str, str]]:
        current = self.header.forward[0]
        while current:
            yield current.key, current.value
            current = current.forward[0]

    def clear(self) -> None:
        self.level = 1
        self.header = SkipListNode(key="", value="", level=self.max_level)
        self.size_bytes = 0
        self.entry_count = 0