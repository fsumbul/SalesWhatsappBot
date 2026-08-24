"""Set non-secret production runtime selectors without printing secrets."""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

def _updates(*, llm_provider: str, llm_model: str, llm_base_url: str) -> dict[str, str]:
    """Return non-secret selectors for an explicitly chosen model endpoint."""

    return {
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "WHATSAPP_AGENT_SLUG": "arti-kasnak",
        "LLM_PROVIDER": llm_provider,
        "LLM_MODEL": llm_model,
        "LLM_BASE_URL": llm_base_url,
    }


def configure(
    path: Path,
    *,
    llm_provider: str = "ollama",
    llm_model: str = "qwen3:8b",
    llm_base_url: str = "http://127.0.0.1:11434/v1",
) -> Path | None:
    if not path.is_file():
        raise SystemExit(f"env file not found: {path}")
    original = path.read_text(encoding="utf-8-sig")

    updates = _updates(
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_base_url=llm_base_url,
    )
    seen: set[str] = set()
    output: list[str] = []
    for raw_line in original.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in raw_line:
            key = raw_line.split("=", 1)[0].strip()
            if key in updates:
                if key not in seen:
                    output.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        output.append(raw_line)
    if output and output[-1]:
        output.append("")
    output.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
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
    parser.add_argument(
        "--llm-provider", choices=["ollama", "openai_compatible"], default="ollama"
    )
    parser.add_argument("--llm-model", default="qwen3:8b")
    parser.add_argument("--llm-base-url", default="http://127.0.0.1:11434/v1")
    args = parser.parse_args()
    backup = configure(
        args.env_file,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
    )
    updates = _updates(
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
    )
    print(f"configured keys: {', '.join(updates)}")
    print(f"backup: {backup}" if backup is not None else "configuration already current")


if __name__ == "__main__":
    main()
