"""Per-window undo/redo stacks in Redis, matching UndoRedoStackService keys.

Stack entries are stored under ``operations:{undo|redo}:{userId}:{tableId}:
{windowId}`` as JSON arrays through the shared keyv-compatible CacheService, so
the wire layout matches the upstream stack for a swapped-in deployment.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ...core import cls
from ...core.cache import CacheService, get_cache

MAX_UNDO_STACK_SIZE = 200
UNDO_EXPIRATION_TIME = 86400

Operation = dict[str, Any]


class UndoRedoStackService:
    def __init__(self, cache: CacheService | None = None) -> None:
        self._cache = cache or get_cache()

    @staticmethod
    def _undo_key(user_id: str, table_id: str, window_id: str) -> str:
        return f"operations:undo:{user_id}:{table_id}:{window_id}"

    @staticmethod
    def _redo_key(user_id: str, table_id: str, window_id: str) -> str:
        return f"operations:redo:{user_id}:{table_id}:{window_id}"

    async def _get(self, key: str) -> list[Operation]:
        return (await self._cache.get(key)) or []

    async def _set(self, key: str, stack: list[Operation]) -> None:
        await self._cache.set(key, stack, UNDO_EXPIRATION_TIME)

    async def push(
        self, user_id: str, table_id: str, window_id: str, operation: Operation
    ) -> None:
        undo_key = self._undo_key(user_id, table_id, window_id)
        stack = await self._get(undo_key)
        stack.append(operation)
        if len(stack) > MAX_UNDO_STACK_SIZE:
            stack = stack[-MAX_UNDO_STACK_SIZE:]
        await self._set(undo_key, stack)
        await self._cache.delete(self._redo_key(user_id, table_id, window_id))

    async def pop_undo(
        self, table_id: str, window_id: str
    ) -> tuple[Operation | None, Callable[[Operation], Awaitable[None]]]:
        user_id = cls.get("user.id")
        undo_key = self._undo_key(user_id, table_id, window_id)
        redo_key = self._redo_key(user_id, table_id, window_id)
        undo_stack = await self._get(undo_key)
        redo_stack = await self._get(redo_key)
        operation = undo_stack.pop() if undo_stack else None

        async def push(new_operation: Operation) -> None:
            redo_stack.append(new_operation)
            await self._set(undo_key, undo_stack)
            await self._set(redo_key, redo_stack)

        return operation, push

    async def pop_redo(
        self, table_id: str, window_id: str
    ) -> tuple[Operation | None, Callable[[Operation], Awaitable[None]]]:
        user_id = cls.get("user.id")
        undo_key = self._undo_key(user_id, table_id, window_id)
        redo_key = self._redo_key(user_id, table_id, window_id)
        undo_stack = await self._get(undo_key)
        redo_stack = await self._get(redo_key)
        operation = redo_stack.pop() if redo_stack else None

        async def push(new_operation: Operation) -> None:
            undo_stack.append(new_operation)
            await self._set(undo_key, undo_stack)
            await self._set(redo_key, redo_stack)

        return operation, push


async def capture_operation(table_id: str, operation: Operation) -> None:
    """Push an operation onto the current window's undo stack, if capturable.

    Skipped while replaying (undo/redo re-runs the same writes) or while a
    composite op is being assembled (paste captures one entry, not its parts).
    """
    if cls.get("undoRedoReplaying") or cls.get("undoRedoSuppressCapture"):
        return
    window_id = cls.get("windowId")
    user_id = cls.get("user.id")
    if not window_id or not user_id:
        return
    await UndoRedoStackService().push(user_id, table_id, window_id, operation)
