"""Create ignored local BI credentials without printing secrets.

This is for the single-machine Docker Compose demo. Existing .env files are
never changed, so rerunning it cannot rotate a live Metabase database password.
"""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def bootstrap(path: Path = ROOT / ".env") -> Path:
    if path.exists():
        raise FileExistsError(f"{path} already exists; keep its current credentials")
    values = {
        "MYSQL_ROOT_PASSWORD": os.environ.get("MYSQL_ROOT_PASSWORD", "energy_root_pwd"),
        "METABASE_DB_PASSWORD": secrets.token_urlsafe(32),
        "BI_DB_PASSWORD": secrets.token_urlsafe(32),
        "METABASE_ADMIN_EMAIL": "energy.admin@example.com",
        "METABASE_ADMIN_PASSWORD": secrets.token_urlsafe(32),
    }
    text = "# Local-only credentials. This file is ignored by Git.\n"
    text += "".join(f"{key}={value}\n" for key, value in values.items())
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def rotate_reader(path: Path = ROOT / ".env") -> Path:
    """Rotate only the local BI reader password after an accidental disclosure."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines)
               if line.startswith("BI_DB_PASSWORD=")]
    if len(matches) != 1:
        raise ValueError("expected exactly one BI_DB_PASSWORD entry")
    lines[matches[0]] = f"BI_DB_PASSWORD={secrets.token_urlsafe(32)}\n"
    next_path = path.with_name(f"{path.name}.next-{secrets.token_hex(6)}")
    with next_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.writelines(lines)
    os.replace(next_path, path)
    return path


def add_viewer(path: Path = ROOT / ".env") -> Path:
    """Store a local non-admin test login without changing existing passwords."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if any(line.startswith(("METABASE_VIEWER_EMAIL=", "METABASE_VIEWER_PASSWORD="))
           for line in lines):
        raise ValueError("Metabase viewer credentials already exist")
    lines.extend((
        "METABASE_VIEWER_EMAIL=energy.viewer@example.com\n",
        f"METABASE_VIEWER_PASSWORD={secrets.token_urlsafe(32)}\n",
    ))
    next_path = path.with_name(f"{path.name}.next-{secrets.token_hex(6)}")
    with next_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.writelines(lines)
    os.replace(next_path, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--rotate-reader", action="store_true")
    actions.add_argument("--add-viewer", action="store_true")
    args = parser.parse_args()
    if args.rotate_reader:
        path = rotate_reader()
        print(f"Rotated BI reader password in {path}; credential was not printed")
    elif args.add_viewer:
        path = add_viewer()
        print(f"Added Metabase viewer credentials to {path}; password was not printed")
    else:
        path = bootstrap()
        print(f"Created {path}; credentials were not printed")


if __name__ == "__main__":
    main()
