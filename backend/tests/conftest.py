"""Cross-platform asyncio loop required by psycopg async connections on Windows."""

import asyncio
import sys
from collections.abc import Callable, Mapping

from pytest import Config, Item


def _compatible_loop() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def pytest_asyncio_loop_factories(
    config: Config, item: Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    del config, item
    return {"psycopg-compatible": _compatible_loop}
