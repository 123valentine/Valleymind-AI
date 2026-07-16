import argparse
import shutil
from pathlib import Path


USERS_ROOT = Path(__file__).resolve().parent.parent / "memory_data" / "users"


def safe_remove_test_memory(path: Path, dry_run: bool = False) -> str:
    root = USERS_ROOT.resolve()
    target = path.resolve()

    if not target.is_dir():
        return f"skip missing/non-directory: {target}"
    if root not in target.parents:
        raise RuntimeError(f"Refusing to delete outside test memory root: {target}")
    if "test" not in target.name.lower():
        raise RuntimeError(f"Refusing to delete non-test memory directory: {target}")

    if dry_run:
        return f"would remove: {target}"
    shutil.rmtree(target)
    return f"removed: {target}"


def main():
    parser = argparse.ArgumentParser(
        description="Safely remove only test-specific Valleymind memory directories."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting anything.",
    )
    args = parser.parse_args()

    if not USERS_ROOT.exists():
        print(f"No users directory found: {USERS_ROOT}")
        return 0

    for child in sorted(USERS_ROOT.iterdir()):
        if child.is_dir() and "test" in child.name.lower():
            print(safe_remove_test_memory(child, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
