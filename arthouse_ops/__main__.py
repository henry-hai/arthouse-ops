"""Command line entry point: python -m arthouse_ops"""

import argparse
import sys

from . import classify, config, logs, sheets, wordpress
from .run import NullSheet, Run


def main(argv=None):
    parser = argparse.ArgumentParser(prog="arthouse-ops", description=__doc__)
    parser.add_argument("--source", choices=("registration", "contact_us", "all"),
                        default="all", help="which form path to run")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and classify but write nothing, and need no Google credential")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many entries are classified")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logs.setup(args.log_level)
    settings = config.load(args.env_file)
    sources = ("registration", "contact_us") if args.source == "all" else (args.source,)

    run = Run(
        settings,
        wordpress.Client(settings),
        NullSheet() if args.dry_run else sheets.Sheet(settings),
        classify.Classifier(settings),
        dry_run=args.dry_run,
        limit=args.limit,
    )
    return run.execute(sources)


if __name__ == "__main__":
    sys.exit(main())
