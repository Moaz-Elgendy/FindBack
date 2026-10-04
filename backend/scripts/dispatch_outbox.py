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
from app.services.outbox import (  # noqa: E402
    dispatch_interval, dispatch_once, run_forever,
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI. `--interval` overrides the environment, which sets the default."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="run a single tick and exit")
    interval = dispatch_interval()
    parser.add_argument(
        "--interval", type=int, default=interval,
        help="seconds between ticks when looping "
             f"(default: $OUTBOX_DISPATCH_INTERVAL_SECONDS, else {interval})")
    parser.add_argument("--limit", type=int, default=50,
                        help="max jobs per tick")
    return parser


def main() -> int:
    args = build_parser().parse_args()

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