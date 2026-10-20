"""Turns the web app password into APP_PASSWORD_HASH, in place in .env.

    python scripts/hash_password.py            # reads APP_PASSWORD from .env
    python scripts/hash_password.py --prompt   # asks for it instead

Replaces the APP_PASSWORD line with APP_PASSWORD_HASH (so the plain password
never stays on disk) and adds a random SESSION_SECRET if there's none.
Prints neither the password nor the hash.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.auth import hash_password

MIN_LENGTH = 10


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--prompt", action="store_true")
    args = parser.parse_args()

    lines = args.env_file.read_text(encoding="utf-8").splitlines()
    plain_idx = next((i for i, l in enumerate(lines) if l.startswith("APP_PASSWORD=")), None)

    if args.prompt:
        password = getpass.getpass("Contraseña: ")
        if getpass.getpass("Repítela: ") != password:
            print("No coinciden.", file=sys.stderr)
            return 1
    elif plain_idx is not None:
        password = lines[plain_idx].split("=", 1)[1].strip().strip("'\"")
    else:
        print(f"No hay APP_PASSWORD en {args.env_file}; usa --prompt.", file=sys.stderr)
        return 1

    if len(password) < MIN_LENGTH:
        print(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres.", file=sys.stderr)
        return 1

    hashed = f"APP_PASSWORD_HASH={hash_password(password)}"
    lines = [l for l in lines if not l.startswith("APP_PASSWORD_HASH=")]
    if plain_idx is not None:
        plain_idx = next(i for i, l in enumerate(lines) if l.startswith("APP_PASSWORD="))
        lines[plain_idx] = hashed
    else:
        lines.append(hashed)
    if not any(l.startswith("SESSION_SECRET=") for l in lines):
        lines.append(f"SESSION_SECRET={secrets.token_urlsafe(48)}")

    args.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"APP_PASSWORD_HASH guardado en {args.env_file} (sin la contraseña en texto plano).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
