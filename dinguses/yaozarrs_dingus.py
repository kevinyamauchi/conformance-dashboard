# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "yaozarrs>=0.3",
#     "pydantic>=2",
# ]
# ///
"""oztest dingus for yaozarrs. See README.md for the contract.

    uv run --script dinguses/yaozarrs_dingus.py --describe
    uv run --script dinguses/yaozarrs_dingus.py <case.json>

Run by scripts/run_conformance_tests.py, which discovers this file and
hands it to `oztest test`.
"""
import argparse
import json
import sys
from pathlib import Path

ID = "yaozarrs"
NAME = "yaozarrs"
HOMEPAGE = "https://github.com/imaging-formats/yaozarrs"
PACKAGE = "yaozarrs"


def describe() -> int:
    """Print this tool's identity as JSON.

    Returns
    -------
    int
        The process exit status.
    """
    from importlib.metadata import version

    print(
        json.dumps(
            {
                "id": ID,
                "name": NAME,
                "homepage": HOMEPAGE,
                "version": version(PACKAGE),
            }
        )
    )
    return 0


def validate(case_path: Path) -> int:
    """Run the yaozarrs validator on a single path.

    Parameters
    ----------
    case_path : Path
        Path to the JSON file holding the Zarr attributes to validate.

    Returns
    -------
    int
        The process exit status. Returns 0 if the validator was able to
        process the JSON (irrespective if the JSON was found valid or invalid).
    """
    import yaozarrs
    from pydantic import ValidationError

    try:
        yaozarrs.validate_ome_json(case_path.read_text())
    except ValidationError:
        print(json.dumps({"validity": "invalid", "message": "ValidationError"}))
        return 0
    print(json.dumps({"validity": "valid"}))
    return 0


def parse_args() -> argparse.Namespace:
    """Parse the single argument oztest or the runner passes."""
    parser = argparse.ArgumentParser(
        description=f"oztest dingus for {NAME}; see dinguses/README.md."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--describe",
        action="store_true",
        help="Print this tool's id, name, homepage and version as JSON.",
    )
    mode.add_argument(
        "case_path",
        nargs="?",
        type=Path,
        help="Path to a parse_attributes test case to validate.",
    )
    return parser.parse_args()


def main() -> int:
    """Dispatch to whichever mode was requested."""
    args = parse_args()
    if args.describe:
        return describe()
    return validate(args.case_path)


if __name__ == "__main__":
    sys.exit(main())
