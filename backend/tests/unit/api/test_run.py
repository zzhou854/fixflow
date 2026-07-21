import asyncio

from app.api.run import selector_event_loop


def test_api_host_uses_selector_event_loop_for_async_psycopg() -> None:
    loop = selector_event_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
