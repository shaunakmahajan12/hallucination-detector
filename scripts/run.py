"""Start the API with uvicorn, using host and port from HD_* settings."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import uvicorn  # noqa: E402

from api.settings import Settings  # noqa: E402

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run("api.main:create_app", factory=True, host=settings.host, port=settings.port, workers=1,
                log_level="info")
