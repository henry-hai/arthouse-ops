#!/usr/bin/env python3
"""Save one real page of connector entries to inputs/ for local testing.

The output holds real names, emails and message bodies, so inputs/ is
gitignored and this file never leaves the machine that ran it. The tests in
tests/test_recorded.py use it when it is there and skip when it is not.

    python scripts/record-sample.py --form 3 --limit 200
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from arthouse_ops import config, logs, wordpress  # noqa: E402

FORMS = {"registration": "registration_form_id", "contact_us": "contact_us_form_id"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--form", choices=sorted(FORMS), default="contact_us")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()

    logs.setup()
    settings = config.load()
    settings = type(settings)(**{**settings.__dict__, "page_size": args.limit,
                                 "max_pages": 1, "start_offset": args.offset})
    form_id = getattr(settings, FORMS[args.form])
    page = wordpress.Client(settings).fetch_all(form_id)[0]

    out = pathlib.Path("inputs") / f"{args.form}-page.json"
    out.write_text(json.dumps(page, indent=2, ensure_ascii=False))
    print(f"wrote {out} with {len(page.get('entries', []))} entries")


if __name__ == "__main__":
    main()
