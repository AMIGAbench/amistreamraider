"""Standalone launcher for the FastAPI application.

Allows running `python -m server.web` to start the development server
without relying on `uvicorn` CLI being installed globally.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("server.web.app:app", host="0.0.0.0", port=8080, reload=True)


if __name__ == "__main__":
    main()

