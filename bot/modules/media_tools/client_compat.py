"""
Compatibility shim: makes @Client.on_message work in media_tools
by providing a fake Client class that registers handlers on TgClient.bot.
"""
from pyrogram import filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler


class _FakeClient:
    """Mimics pyrogram.Client decorator API, registers on TgClient.bot at import time."""

    def on_message(self, filters_=None, group=0):
        def decorator(func):
            from bot.core.telegram_manager import TgClient
            handler = MessageHandler(func, filters=filters_)
            # Register lazily at first call
            _pending_handlers.append(("message", handler, group))
            return func
        return decorator

    def on_callback_query(self, filters_=None, group=0):
        def decorator(func):
            _pending_handlers.append(("callback", CallbackQueryHandler(func, filters=filters_), group))
            return func
        return decorator


_pending_handlers = []   # list of (type, handler, group)
Client = _FakeClient()


def register_all_handlers():
    """Call this after TgClient.bot is ready to register all pending handlers."""
    from bot.core.telegram_manager import TgClient
    for htype, handler, group in _pending_handlers:
        try:
            TgClient.bot.add_handler(handler, group)
        except Exception as e:
            from bot import LOGGER
            LOGGER.warning(f"Handler registration failed: {e}")
    _pending_handlers.clear()
