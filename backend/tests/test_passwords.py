"""The dashboard password is kept only as a salted scrypt hash."""

import io

import pytest

from ipo_intel.cli import main
from ipo_intel.passwords import check_hash, hash_password, verify_password
from tests.fakes import make_settings


def test_a_hash_verifies_only_its_own_password():
    stored = hash_password("Tulip@456")
    assert stored.startswith("scrypt:16384:8:1:")
    assert "Tulip@456" not in stored and "$" not in stored
    assert verify_password("Tulip@456", stored)
    assert not verify_password("tulip@456", stored)
    assert not verify_password("", stored)


def test_each_hash_has_its_own_salt():
    first, second = hash_password("same"), hash_password("same")
    assert first != second
    assert verify_password("same", first) and verify_password("same", second)


@pytest.mark.parametrize("stored", [
    "", "Tulip@456", "bcrypt:1:2:3:abc:def", "scrypt:16384:8:1:abc", "scrypt:1000:8:1:c2FsdA:a2V5a2V5a2V5a2V5a2V5",
    "scrypt:x:8:1:c2FsdA:a2V5a2V5a2V5a2V5a2V5", "scrypt:16384:8:1::a2V5a2V5a2V5a2V5a2V5", "scrypt:16384:8:1:c2FsdA:a2V5",
])
def test_malformed_hashes_are_rejected(stored):
    with pytest.raises(ValueError):
        check_hash(stored)
    assert not verify_password("anything", stored)


def test_an_empty_password_cannot_be_hashed():
    with pytest.raises(ValueError):
        hash_password("")


@pytest.mark.parametrize("piped", ["Tulip@456\r\n", "Tulip@456", "﻿Tulip@456\n"])
def test_hash_password_command_reads_stdin(monkeypatch, capsys, piped):
    # The last case is what Windows PowerShell sends for `"..." | ipo-intel hash-password`.
    monkeypatch.setattr("sys.stdin", io.StringIO(piped))
    assert main(["hash-password"], settings=make_settings()) == 0
    out = capsys.readouterr().out.strip()
    check_hash(out)
    assert verify_password("Tulip@456", out)


def test_hash_password_command_refuses_an_empty_password(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert main(["hash-password"], settings=make_settings()) == 2
    assert "cannot be empty" in capsys.readouterr().err
