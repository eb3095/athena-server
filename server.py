"""Athena Server - thin wrapper for uvicorn."""

import uvicorn

from athena.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5003)
