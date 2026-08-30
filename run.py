#!/usr/bin/env python3
"""LogAi entrypoint — starts the syslog listeners and the dashboard API together."""
import asyncio
import contextlib

import uvicorn

from logai import db, scoring
from logai.api import app
from logai.config import settings
from logai.syslog_server import start_listeners


async def _rescore_loop():
    while True:
        await asyncio.sleep(15)
        with contextlib.suppress(Exception):
            scoring.rescore_all(settings.alert_score_threshold)


async def _auto_analyze_loop():
    from logai import agent
    while True:
        await asyncio.sleep(settings.auto_analyze_seconds)
        with contextlib.suppress(Exception):
            await agent.run_triage()


async def main():
    db.init_db()
    await start_listeners(settings.syslog_bind, settings.syslog_udp_port,
                          settings.syslog_tcp_port)
    asyncio.create_task(_rescore_loop())
    if settings.auto_analyze_seconds > 0:
        asyncio.create_task(_auto_analyze_loop())
        print(f"[agent] auto-analyze every {settings.auto_analyze_seconds}s")

    print(f"[api]    http://localhost:{settings.api_port}")
    print(f"[ai]     provider={settings.provider} model={settings.model} "
          f"enabled={settings.ai_enabled()}")
    config = uvicorn.Config(app, host=settings.api_host, port=settings.api_port,
                            log_level="warning")
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[logai] stopped")
