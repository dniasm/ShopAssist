"""Run the orchestrator: python -m orchestrator.main"""
import os

import uvicorn
from dotenv import load_dotenv

from orchestrator.app import create_app
from orchestrator.settings import Settings
from shared_common.common.log import setup_logging


def main() -> None:
    load_dotenv()
    setup_logging("orchestrator")
    settings = Settings.from_env()
    # 127.0.0.1 locally; in Docker set ORCHESTRATOR_HOST=0.0.0.0 so the container accepts outside connections.
    uvicorn.run(create_app(settings), host=os.environ.get("ORCHESTRATOR_HOST") or "127.0.0.1", port=settings.port)


if __name__ == "__main__":
    main()
