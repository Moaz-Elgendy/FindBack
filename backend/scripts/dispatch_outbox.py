"""Run the Phase 5 outbox dispatcher against the configured database.

    python -m scripts.dispatch_outbox            # loop forever
    python -m scripts.dispatch_outbox --once     # single tick

Publishes still go to the EXISTING Celery queue; this only retries the intents
that a failed publish left behind.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal  # noqa: E402
from app.services.outbox import dispatch_once, run_forever  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="run a single tick and exit")
    parser.add_argument("--interval", type=int, default=5,
                        help="seconds between ticks when looping")
    parser.add_argument("--limit", type=int, default=50,
                        help="max jobs per tick")
    args = parser.parse_args()

    if args.once:
        db = SessionLocal()
        try:
            print(dispatch_once(db, limit=args.limit))
        finally:
            db.close()
        return 0
    run_forever(SessionLocal, interval=args.interval, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())