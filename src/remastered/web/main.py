from __future__ import annotations

import uvicorn

from remastered.web.settings import WebSettings


def run() -> None:
    ws = WebSettings()
    uvicorn.run(
        "remastered.web.app:app",
        host=ws.host,
        port=ws.port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
