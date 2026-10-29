import logging
from pathlib import Path

from bot.app import create_app
from bot.config import Settings, load_env_file

load_env_file(Path(__file__).resolve().parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

application = create_app(Settings.from_env())
