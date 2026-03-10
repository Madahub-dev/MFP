"""Unit tests for mfp/storage/crypto.py (I-13 §6)."""

from mfp.core.types import StateValue
from mfp.storage.crypto import (
    decrypt_cell,
    derive_cell_key,
    derive_cell_nonce,
    derive_storage_key,
    encrypt_cell,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _master_key() -> bytes:
    return b"\xaa" * 32


def _runtime_id() -> bytes:
    return b"\xbb" * 16


def _storage_key() -> StateValue:
    return derive_storage_key(_master_key(), _runtime_id())


# ---------------------------------------------------------------------------
# derive_storage_key()
# ---------------------------------------------------------------------------

class TestDeriveStorageKey:
    def test_returns_32_byte_state_value(self):
        key = derive_storage_key(_master_key(), _runtime_id())
        assert isinstance(key, StateValue)
        assert len(key.data) == 32

    def test_deterministic(self):
        a = derive_storage_key(_master_key(), _runtime_id())
        b = derive_storage_key(_master_key(), _runtime_id())
        assert a == b

    def test_different_master_key(self):
        a = derive_storage_key(b"\x01" * 32, _runtime_id())
        b = derive_storage_key(b"\x02" * 32, _runtime_id())
        assert a != b

    def test_different_runtime_id(self):
        a = derive_storage_key(_master_key(), b"\x01" * 16)
        b = derive_storage_key(_master_key(), b"\x02" * 16)
        assert a != b


# ---------------------------------------------------------------------------
# derive_cell_key()
# ---------------------------------------------------------------------------

class TestDeriveCellKey:
    def test_returns_32_byte_state_value(self):
        key = derive_cell_key(
            _storage_key(), b"channels", b"local_state", b"\x01" * 16,
        )
        assert isinstance(key, StateValue)
        assert len(key.data) == 32

    def test_different_table(self):
        a = derive_cell_key(_storage_key(), b"channels", b"col", b"\x01" * 16)
        b = derive_cell_key(_storage_key(), b"bilateral", b"col", b"\x01" * 16)
        assert a != b

    def test_different_column(self):
        a = derive_cell_key(_storage_key(), b"channels", b"col_a", b"\x01" * 16)
        b = derive_cell_key(_storage_key(), b"channels", b"col_b", b"\x01" * 16)
        assert a != b

    def test_different_row(self):
        a = derive_cell_key(_storage_key(), b"channels", b"col", b"\x01" * 16)
        b = derive_cell_key(_storage_key(), b"channels", b"col", b"\x02" * 16)
        assert a != b


# ---------------------------------------------------------------------------
# derive_cell_nonce()
# ---------------------------------------------------------------------------

class TestDeriveCellNonce:
    def test_returns_12_bytes(self):
        cell_key = derive_cell_key(
            _storage_key(), b"channels", b"local_state", b"\x01" * 16,
        )
        nonce = derive_cell_nonce(cell_key, b"\x01" * 16, b"local_state")
        assert len(nonce) == 12


# ---------------------------------------------------------------------------
# encrypt_cell() / decrypt_cell()
# ---------------------------------------------------------------------------

class TestEncryptDecryptCell:
    def test_roundtrip(self):
        sk = _storage_key()
        plaintext = b"secret state data " + b"\xff" * 14
        encrypted = encrypt_cell(sk, b"channels", b"local_state", b"\x01" * 16, plaintext)
        decrypted = decrypt_cell(sk, b"channels", b"local_state", b"\x01" * 16, encrypted)
        assert decrypted == plaintext

    def test_tampered_returns_none(self):
        sk = _storage_key()
        plaintext = b"secret"
        encrypted = encrypt_cell(sk, b"channels", b"local_state", b"\x01" * 16, plaintext)
        tampered = bytearray(encrypted)
        tampered[-1] ^= 0xFF
        result = decrypt_cell(sk, b"channels", b"local_state", b"\x01" * 16, bytes(tampered))
        assert result is None

    def test_wrong_key_returns_none(self):
        sk1 = derive_storage_key(b"\x01" * 32, b"\xaa" * 16)
        sk2 = derive_storage_key(b"\x02" * 32, b"\xaa" * 16)
        plaintext = b"secret"
        encrypted = encrypt_cell(sk1, b"channels", b"local_state", b"\x01" * 16, plaintext)
        result = decrypt_cell(sk2, b"channels", b"local_state", b"\x01" * 16, encrypted)
        assert result is None

    def test_different_row_produces_different_ciphertext(self):
        sk = _storage_key()
        plaintext = b"same plaintext for both cells"
        enc_a = encrypt_cell(sk, b"channels", b"local_state", b"\x01" * 16, plaintext)
        enc_b = encrypt_cell(sk, b"channels", b"local_state", b"\x02" * 16, plaintext)
        assert enc_a != enc_b
