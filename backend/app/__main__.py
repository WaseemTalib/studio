"""Start the server with the host and port from backend/.env: `.venv/bin/python -m app` (run in backend/)."""

import uvicorn

from .config import HOST, PORT

uvicorn.run("app.main:app", host=HOST, port=PORT, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
            timeout_graceful_shutdown=3)
