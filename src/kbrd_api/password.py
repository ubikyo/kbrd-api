import hashlib
import hmac
import os

"""
The one secret the keyboard keeps: what KBRD-WEB asks for before it hands
the app over. Set once, in the first-run wizard (see `api/setup.py`).

Never stored as it was typed — what goes in the database is a PBKDF2
digest and the salt it was taken with, in one self-describing string, so
a database read off a card says nothing about the password itself.

The work factor is written into every hash rather than assumed, which is
what lets it be raised later without the hashes already in the database
becoming unreadable: `verify` reads each one's own.
"""

# What KBRD-WEB's own field allows, and the range this refuses outside
# of. The floor is a real one; the ceiling only stops a body that is a
# file rather than a password.
MIN_LENGTH = 8
MAX_LENGTH = 64

ALGORITHM = "pbkdf2_sha256"
# Chosen against the hardware rather than against a desktop: this runs on
# the keyboard's own CPU, where it costs a fraction of a second — long
# enough to make a stolen database expensive to work through, short
# enough that nobody waits on it to sign in.
ITERATIONS = 100_000
SALT_BYTES = 16


class PasswordError(ValueError):
    """A password this service won't take, in words fit to show."""


def check(value) -> str:
    """The password as it was typed, or a reason it can't be one."""
    if not isinstance(value, str):
        raise PasswordError("invalid password")
    # Not stripped: a space is a character like any other here, and
    # trimming one would quietly store something other than what was
    # typed — which is a password nobody can type again.
    if len(value) < MIN_LENGTH:
        raise PasswordError(
            f"the password must be at least {MIN_LENGTH} characters"
        )
    if len(value) > MAX_LENGTH:
        raise PasswordError(
            f"the password must be at most {MAX_LENGTH} characters"
        )
    return value


def hash_password(password: str) -> str:
    """`algorithm$iterations$salt$digest`, hex either side."""
    salt = os.urandom(SALT_BYTES)
    digest = _digest(password, salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify(password: str, stored: str) -> bool:
    """Whether `password` is the one `stored` was taken from.

    False for anything that isn't a hash this module wrote — an empty
    column (no password set), or a row from some other scheme. A device
    with no password is not a device every password opens.
    """
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != ALGORITHM:
        return False
    _, iterations, salt, digest = parts
    try:
        expected = bytes.fromhex(digest)
        candidate = _digest(password, bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    # Constant time: a comparison that stopped at the first wrong byte
    # would say how much of a guess was right.
    return hmac.compare_digest(candidate, expected)


def _digest(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
