from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet

from .config import get_settings


@lru_cache
def profile_cipher() -> Fernet:
    path = get_settings().profile_key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        key = path.read_bytes().strip()
    except FileNotFoundError:
        generated = Fernet.generate_key()
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            key = path.read_bytes().strip()
        else:
            with os.fdopen(descriptor, "wb") as target:
                target.write(generated)
            key = generated
    return Fernet(key)


def encrypt_profile_value(value: str | None) -> str | None:
    if not value:
        return None
    return profile_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_profile_value(value: str | None) -> str | None:
    if not value:
        return None
    return profile_cipher().decrypt(value.encode("ascii")).decode("utf-8")
