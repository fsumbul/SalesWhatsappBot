"""Package reviewed API source and a built web app, excluding private environments."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def package(output: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    api = repo / "apps/api"
    web = repo / "apps/web"
    if not (web / ".next/BUILD_ID").is_file():
        raise SystemExit("Build the web application first")
    paths: list[tuple[Path, str]] = []
    for folder in ("src", "alembic", "config"):
        paths.extend((p, "api/" + p.relative_to(api).as_posix()) for p in (api / folder).rglob("*")
                     if p.is_file() and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts)
    for name in ("bootstrap_arti_kasnak_agent.py", "bootstrap_platform_owner.py", "bind_runtime_channel.py", "backup_production_database.py", "runtime_preflight.py", "verify_company_models.py", "package_company_release.py"):
        paths.append((api / "scripts" / name, "api/scripts/" + name))
    for name in ("alembic.ini", "pyproject.toml", "poetry.lock"):
        paths.append((api / name, "api/" + name))
    for folder in ("src", "messages", "public", ".next", "ops"):
        paths.extend((p, "web/" + p.relative_to(web).as_posix()) for p in (web / folder).rglob("*")
                     if p.is_file() and "cache" not in p.relative_to(web).parts)
    for name in ("package.json", "pnpm-lock.yaml", "next.config.ts", "tsconfig.json", "next-env.d.ts", "postcss.config.mjs", "tailwind.config.ts"):
        if (web / name).exists():
            paths.append((web / name, "web/" + name))
    manifest = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in paths:
            if path.name.startswith(".env") or path.suffix in {".key", ".pem"}:
                raise SystemExit("Private material must never enter a release")
            data = path.read_bytes()
            manifest[name] = hashlib.sha256(data).hexdigest()
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    print(json.dumps({"path": str(output), "files": len(manifest), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    package(parser.parse_args().output)
