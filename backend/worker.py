"""Ingestion entrypoint: python worker.py."""

import logging
import sys
import time

from app.ingestion import run_once

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        log.info("PDF ingestion worker started")
        while True:
            if not run_once():
                time.sleep(3)
