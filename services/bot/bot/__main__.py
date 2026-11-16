"""python -m bot"""
import os

import uvicorn

from homelab_common import setup_logging

from .app import BotApp


def main():
    setup_logging("bot")
    host, _, port = os.environ.get("LISTEN", "0.0.0.0:8080").rpartition(":")
    uvicorn.run(BotApp().asgi(), host=host, port=int(port), log_level="warning",
                timeout_keep_alive=120)


if __name__ == "__main__":
    main()
