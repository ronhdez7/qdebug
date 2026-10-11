"""Build a clean ZIP that can be uploaded as a Qollab Python project."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
QOLLAB_DIR = ROOT / "qollab"
PACKAGE_DIR = ROOT / "qdebug"


def build_archive(output: Path) -> int:
    """Write the Qollab entry point, requirements, and package to ``output``."""
    package_files = sorted(PACKAGE_DIR.glob("*.py"))
    if not package_files:
        raise FileNotFoundError(f"no Python package files found in {PACKAGE_DIR}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(QOLLAB_DIR / "main.py", "main.py")
        archive.write(QOLLAB_DIR / "requirements.txt", "requirements.txt")
        for source in package_files:
            archive.write(source, f"qdebug/{source.name}")
    return len(package_files) + 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=ROOT / "qollab-upload.zip",
        help="output ZIP path (default: qollab-upload.zip in the repository root)",
    )
    args = parser.parse_args()
    count = build_archive(args.output)
    print(f"Created {args.output.resolve()} with {count} files.")
    print("Archive layout: main.py, requirements.txt, qdebug/*.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())