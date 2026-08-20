"""AIMM_API_KEY / AIMM_AUTH_SECRET: env, then .secrets/, else generate once."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

API_KEY_ENV = "AIMM_API_KEY"
AUTH_SECRET_ENV = "AIMM_AUTH_SECRET"
SECRETS_DIR_ENV = "AIMM_SECRETS_DIR"
DISABLE_GENERATE_ENV = "AIMM_DISABLE_SECRET_GENERATE"

API_KEY_FILENAME = "api_key"
AUTH_SECRET_FILENAME = "auth_secret"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def generate_secret() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def is_usable_secret(value: str | None) -> bool:
    return bool((value or "").strip())


def secrets_dir() -> Path:
    raw = (os.getenv(SECRETS_DIR_ENV) or "").strip()
    if raw:
        return Path(raw)
    return _REPO_ROOT / ".secrets"


def _generate_disabled() -> bool:
    raw = (os.getenv(DISABLE_GENERATE_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _read_secret_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("control-plane: could not read %s", path)
        return None
    return text if is_usable_secret(text) else None


def _write_secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)


def _replace_secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def persist_secrets(api_key: str, auth_secret: str) -> None:
    dest = secrets_dir()
    for name, value in ((API_KEY_FILENAME, api_key), (AUTH_SECRET_FILENAME, auth_secret)):
        path = dest / name
        existing = None
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = None
        if existing == value:
            continue
        _replace_secret_file(path, value)


def _load_or_create_file(path: Path, *, generate: bool) -> str | None:
    existing = _read_secret_file(path)
    if existing:
        return existing
    if not generate:
        return None
    created = generate_secret()
    try:
        _write_secret_file(path, created)
        return created
    except FileExistsError:
        existing = _read_secret_file(path)
        if existing:
            return existing
        path.unlink(missing_ok=True)
        try:
            _write_secret_file(path, created)
            return created
        except FileExistsError:
            return _read_secret_file(path)


def _from_env(name: str) -> str | None:
    raw = (os.getenv(name) or "").strip()
    return raw or None


def resolve_secret(env_name: str, filename: str, *, generate: bool) -> str | None:
    from_env = _from_env(env_name)
    if from_env:
        return from_env
    path = secrets_dir() / filename
    loaded = _load_or_create_file(path, generate=generate and not _generate_disabled())
    if loaded:
        return loaded
    return None


def resolve_api_key(*, generate: bool = False) -> str | None:
    return resolve_secret(API_KEY_ENV, API_KEY_FILENAME, generate=generate)


def resolve_auth_secret(*, generate: bool = False) -> str | None:
    return resolve_secret(AUTH_SECRET_ENV, AUTH_SECRET_FILENAME, generate=generate)


def ensure_control_plane_secrets(*, generate: bool = True) -> tuple[str, str]:
    do_generate = generate and not _generate_disabled()
    api_key = resolve_api_key(generate=do_generate)
    auth_secret = resolve_auth_secret(generate=do_generate)
    missing: list[str] = []
    if not api_key:
        missing.append(API_KEY_ENV)
    if not auth_secret:
        missing.append(AUTH_SECRET_ENV)
    if missing:
        dest = secrets_dir()
        raise RuntimeError(
            f"Missing {', '.join(missing)}. Set them in the environment or run "
            f"python -m api.control_plane_secrets --write (writes under {dest})."
        )
    os.environ[API_KEY_ENV] = api_key
    os.environ[AUTH_SECRET_ENV] = auth_secret
    return api_key, auth_secret


def presented_matches(presented: str | None, expected: str) -> bool:
    import secrets as _secrets

    if not presented or not expected:
        return False
    try:
        return _secrets.compare_digest(presented, expected)
    except (TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or load AIMM_API_KEY and AIMM_AUTH_SECRET."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist secrets under AIMM_SECRETS_DIR (or <repo>/.secrets).",
    )
    parser.add_argument(
        "--export-shell",
        action="store_true",
        help="Print `export VAR=...` lines for eval in an entrypoint.",
    )
    args = parser.parse_args(argv)

    try:
        api_key, auth_secret = ensure_control_plane_secrets(generate=True)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    dest = secrets_dir()
    if args.write:
        persist_secrets(api_key, auth_secret)
    if args.write or not args.export_shell:
        print(f"secrets ready under {dest}/ ({API_KEY_FILENAME}, {AUTH_SECRET_FILENAME})")

    if args.export_shell:
        print(f"export {API_KEY_ENV}={shlex.quote(api_key)}")
        print(f"export {AUTH_SECRET_ENV}={shlex.quote(auth_secret)}")
        print(f"export {SECRETS_DIR_ENV}={shlex.quote(str(dest))}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
