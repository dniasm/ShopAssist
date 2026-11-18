"""Run the orchestrator: python -m orchestrator.main"""
import logging

import uvicorn
from dotenv import load_dotenv

from orchestrator.app import create_app
from orchestrator.settings import Settings


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s orchestrator %(levelname)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port)


if __name__ == "__main__":
    main()
