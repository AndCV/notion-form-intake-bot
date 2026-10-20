from lib.auth import hash_password, verify_password


def test_hash_verifies_only_the_right_password():
    """protects: login accepts the owner's password and nothing else;
    fails_when: a wrong password verifies or the right one doesn't;
    no_claim: says nothing about brute-force cost tuning."""
    stored = hash_password("correct horse battery")

    assert stored.startswith("scrypt$")
    assert "correct horse battery" not in stored
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterY", stored)


def test_hashes_are_salted():
    """protects: two hashes of the same password differ;
    fails_when: the salt is constant;
    no_claim: none beyond protects."""
    assert hash_password("same password") != hash_password("same password")


def test_malformed_stored_hash_never_verifies():
    """protects: a broken APP_PASSWORD_HASH locks the door instead of opening it;
    fails_when: a malformed value raises or returns True;
    no_claim: none beyond protects."""
    for stored in ("", "plain-text", "bcrypt$1$2$3$aa$bb", "scrypt$x$8$1$zz$yy"):
        assert not verify_password("anything", stored)
