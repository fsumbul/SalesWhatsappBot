"""Create a pre-deployment PostgreSQL custom-format backup without leaking credentials."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(r"C:\sites\ashiraai\backups"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    url = make_url(str(settings.migrations_database_url or settings.database_url))
    pg_dump = shutil.which("pg_dump") or r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
    if not Path(pg_dump).is_file():
        raise SystemExit("pg_dump was not found")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output = args.output_dir / f"db-pre-agent-runtime-{stamp}.dump"
    env = os.environ.copy()
    env["PGPASSWORD"] = url.password or ""
    command = [
        pg_dump,
        "--host",
        url.host or "127.0.0.1",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "postgres",
        "--dbname",
        url.database or "postgres",
        "--format=custom",
        "--enable-row-security",
        "--file",
        str(output),
    ]
    subprocess.run(command, env=env, check=True, capture_output=True)
    print(f"backup: {output}")
    print(f"bytes: {output.stat().st_size}")


if __name__ == "__main__":
    main()
