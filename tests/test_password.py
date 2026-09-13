import unittest

from kbrd_api import password


class PasswordHashTest(unittest.TestCase):
    """What is stored, and what it answers to."""

    def test_a_hash_says_how_it_was_taken(self):
        stored = password.hash_password("hunter22")
        algorithm, iterations, salt, digest = stored.split("$")
        self.assertEqual(algorithm, password.ALGORITHM)
        self.assertEqual(int(iterations), password.ITERATIONS)
        # Hex both, and the salt is the length this module asks for.
        self.assertEqual(len(bytes.fromhex(salt)), password.SALT_BYTES)
        self.assertEqual(len(bytes.fromhex(digest)), 32)

    def test_the_password_is_nowhere_in_it(self):
        self.assertNotIn("hunter22", password.hash_password("hunter22"))

    def test_two_hashes_of_one_password_differ(self):
        # Salted per hash, so a database can't be read for two accounts
        # sharing a password — nor for the same one set twice.
        first = password.hash_password("hunter22")
        self.assertNotEqual(first, password.hash_password("hunter22"))
        self.assertTrue(password.verify("hunter22", first))

    def test_it_answers_to_that_password_and_no_other(self):
        stored = password.hash_password("hunter22")
        self.assertTrue(password.verify("hunter22", stored))
        self.assertFalse(password.verify("hunter23", stored))
        self.assertFalse(password.verify("HUNTER22", stored))
        self.assertFalse(password.verify("", stored))

    def test_a_device_with_no_password_is_not_one_every_password_opens(self):
        # The column starts empty (see `db.init_schema`), and nothing is
        # the right answer to it.
        for stored in ("", "not a hash", "pbkdf2_sha256$x$y", "md5$1$aa$bb"):
            with self.subTest(stored):
                self.assertFalse(password.verify("hunter22", stored))

    def test_an_iteration_count_is_read_off_the_hash_it_was_taken_with(self):
        # What lets the work factor be raised later without the hashes
        # already stored becoming unreadable.
        salt = b"\x01" * password.SALT_BYTES
        digest = password._digest("hunter22", salt, 1000)
        stored = f"{password.ALGORITHM}$1000${salt.hex()}${digest.hex()}"
        self.assertTrue(password.verify("hunter22", stored))


class PasswordCheckTest(unittest.TestCase):
    """The range KBRD-API takes, which is KBRD-WEB's own field's."""

    def test_it_gives_back_exactly_what_was_typed(self):
        self.assertEqual(password.check("hunter22"), "hunter22")
        # Spaces included, at either end: trimming would store something
        # other than what was typed.
        self.assertEqual(password.check("  spaces  "), "  spaces  ")

    def test_it_refuses_one_too_short_or_too_long(self):
        with self.assertRaises(password.PasswordError):
            password.check("short")
        with self.assertRaises(password.PasswordError):
            password.check("x" * (password.MAX_LENGTH + 1))

    def test_it_refuses_what_is_not_a_password_at_all(self):
        for value in (None, 12345678, ["hunter22"]):
            with self.subTest(value):
                with self.assertRaises(password.PasswordError):
                    password.check(value)

    def test_its_refusals_are_value_errors(self):
        # Which is what `api/setup.py` catches them as, alongside every
        # other thing it refuses in one body.
        self.assertTrue(issubclass(password.PasswordError, ValueError))
