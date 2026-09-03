from __future__ import annotations

import time

from backend.app.database import SessionLocal
from backend.app.services.render_worker import process_next_job


def main() -> None:
    while True:
        with SessionLocal() as db:
            processed = process_next_job(db)
        if processed is None:
            time.sleep(2)


if __name__ == "__main__":
    main()
