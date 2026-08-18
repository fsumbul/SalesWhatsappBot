"""Set non-secret production runtime selectors without printing secrets."""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

_UPDATES = {
    "APP_ENV": "production",
    "APP_DEBUG": "false",
    "WHATSAPP_AGENT_SLUG": "arti-kasnak",
    "LLM_PROVIDER": "ollama",
    "LLM_MODEL": "qwen3:8b",
    # The Arch host exposes Ollama to Windows only through a loopback-bound
    # reverse SSH tunnel. Ollama itself is never made public.
    "LLM_BASE_URL": "http://127.0.0.1:11434/v1",
}


def configure(path: Path) -> Path | None:
    if not path.is_file():
        raise SystemExit(f"env file not found: {path}")
    original = path.read_text(encoding="utf-8-sig")

    seen: set[str] = set()
    output: list[str] = []
    for raw_line in original.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in raw_line:
            key = raw_line.split("=", 1)[0].strip()
            if key in _UPDATES:
                if key not in seen:
                    output.append(f"{key}={_UPDATES[key]}")
                seen.add(key)
                continue
        output.append(raw_line)
    if output and output[-1]:
        output.append("")
    output.extend(f"{key}={value}" for key, value in _UPDATES.items() if key not in seen)
    configured = "\n".join(output).rstrip() + "\n"
    if configured == original:
        return None

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.runtime-{stamp}.bak")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.runtime-{stamp}-{suffix}.bak")
        suffix += 1
    shutil.copy2(path, backup)
    path.write_text(configured, encoding="utf-8")
    return backup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env"
    )
    args = parser.parse_args()
    backup = configure(args.env_file)
    print(f"configured keys: {', '.join(_UPDATES)}")
    print(f"backup: {backup}" if backup is not None else "configuration already current")


if __name__ == "__main__":
    main()
