"""Unit tests for mfp/core/primitives.py (I-02)."""

import pytest

from mfp.core.primitives import (
    ChaCha20PRNG,
    aes_256_gcm_decrypt,
    aes_256_gcm_encrypt,
    constant_time_equal,
    encode_u64_be,
    hmac_sha256,
    random_block,
    random_bytes,
    random_id,
    random_state_value,
    sha256,
)
from mfp.core.types import BLOCK_SIZE, STATE_SIZE, Block, StateValue


# ---------------------------------------------------------------------------
# HMAC-SHA-256
# ---------------------------------------------------------------------------

class TestHmacSha256:
    def test_returns_state_value(self):
        result = hmac_sha256(b"key", b"message")
        assert isinstance(result, StateValue)
        assert len(result.data) == 32

    def test_deterministic(self):
        a = hmac_sha256(b"key", b"msg")
        b = hmac_sha256(b"key", b"msg")
        assert a == b

    def test_different_keys(self):
        a = hmac_sha256(b"key1", b"msg")
        b = hmac_sha256(b"key2", b"msg")
        assert a != b

    def test_different_messages(self):
        a = hmac_sha256(b"key", b"msg1")
        b = hmac_sha256(b"key", b"msg2")
        assert a != b

    def test_empty_message(self):
        result = hmac_sha256(b"key", b"")
        assert isinstance(result, StateValue)

    def test_empty_key(self):
        result = hmac_sha256(b"", b"message")
        assert isinstance(result, StateValue)


# ---------------------------------------------------------------------------
# SHA-256
# ---------------------------------------------------------------------------

class TestSha256:
    def test_returns_state_value(self):
        result = sha256(b"data")
        assert isinstance(result, StateValue)
        assert len(result.data) == 32

    def test_deterministic(self):
        a = sha256(b"data")
        b = sha256(b"data")
        assert a == b

    def test_different_inputs(self):
        a = sha256(b"data1")
        b = sha256(b"data2")
        assert a != b

    def test_empty_input(self):
        result = sha256(b"")
        assert isinstance(result, StateValue)

    def test_known_value(self):
        # SHA-256 of empty string is a well-known constant
        result = sha256(b"")
        assert result.data.hex() == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )


# ---------------------------------------------------------------------------
# ChaCha20PRNG
# ---------------------------------------------------------------------------

class TestChaCha20PRNG:
    def test_deterministic(self):
        seed = StateValue(b"\x42" * 32)
        p1 = ChaCha20PRNG(seed)
        p2 = ChaCha20PRNG(seed)
        assert p1.next_bytes(64) == p2.next_bytes(64)

    def test_different_seeds(self):
        s1 = StateValue(b"\x01" * 32)
        s2 = StateValue(b"\x02" * 32)
        p1 = ChaCha20PRNG(s1)
        p2 = ChaCha20PRNG(s2)
        assert p1.next_bytes(32) != p2.next_bytes(32)

    def test_sequential_draws(self):
        seed = StateValue(b"\x42" * 32)
        p1 = ChaCha20PRNG(seed)
        p2 = ChaCha20PRNG(seed)
        # Draw in different chunk sizes — same total output
        a = p1.next_bytes(16) + p1.next_bytes(16)
        b = p2.next_bytes(32)
        assert a == b

    def test_returns_correct_length(self):
        seed = StateValue(b"\x00" * 32)
        p = ChaCha20PRNG(seed)
        for n in [1, 16, 32, 64, 128]:
            assert len(p.next_bytes(n)) == n

    def test_non_zero_output(self):
        seed = StateValue(b"\x42" * 32)
        p = ChaCha20PRNG(seed)
        output = p.next_bytes(256)
        assert output != b"\x00" * 256


# ---------------------------------------------------------------------------
# AES-256-GCM
# ---------------------------------------------------------------------------

class TestAes256Gcm:
    def _key_nonce(self):
        return b"\xaa" * 32, b"\xbb" * 12

    def test_encrypt_decrypt_roundtrip(self):
        key, nonce = self._key_nonce()
        ct = aes_256_gcm_encrypt(key, nonce, b"hello", b"aad")
        pt = aes_256_gcm_decrypt(key, nonce, ct, b"aad")
        assert pt == b"hello"

    def test_ciphertext_longer_by_16(self):
        key, nonce = self._key_nonce()
        plain = b"x" * 100
        ct = aes_256_gcm_encrypt(key, nonce, plain, b"aad")
        assert len(ct) == len(plain) + 16  # GCM tag

    def test_tampered_ciphertext(self):
        key, nonce = self._key_nonce()
        ct = aes_256_gcm_encrypt(key, nonce, b"hello", b"aad")
        tampered = ct[:-1] + bytes([ct[-1] ^ 0xFF])
        assert aes_256_gcm_decrypt(key, nonce, tampered, b"aad") is None

    def test_wrong_aad(self):
        key, nonce = self._key_nonce()
        ct = aes_256_gcm_encrypt(key, nonce, b"hello", b"aad1")
        assert aes_256_gcm_decrypt(key, nonce, ct, b"aad2") is None

    def test_wrong_key(self):
        _, nonce = self._key_nonce()
        ct = aes_256_gcm_encrypt(b"\xaa" * 32, nonce, b"hello", b"aad")
        assert aes_256_gcm_decrypt(b"\xcc" * 32, nonce, ct, b"aad") is None

    def test_wrong_nonce(self):
        key, _ = self._key_nonce()
        ct = aes_256_gcm_encrypt(key, b"\xbb" * 12, b"hello", b"aad")
        assert aes_256_gcm_decrypt(key, b"\xcc" * 12, ct, b"aad") is None

    def test_invalid_key_size(self):
        with pytest.raises(ValueError, match="32 bytes"):
            aes_256_gcm_encrypt(b"\x00" * 16, b"\x00" * 12, b"p", b"a")

    def test_invalid_nonce_size(self):
        with pytest.raises(ValueError, match="12 bytes"):
            aes_256_gcm_encrypt(b"\x00" * 32, b"\x00" * 8, b"p", b"a")

    def test_empty_plaintext(self):
        key, nonce = self._key_nonce()
        ct = aes_256_gcm_encrypt(key, nonce, b"", b"aad")
        assert len(ct) == 16  # tag only
        pt = aes_256_gcm_decrypt(key, nonce, ct, b"aad")
        assert pt == b""


# ---------------------------------------------------------------------------
# OS CSPRNG
# ---------------------------------------------------------------------------

class TestCSPRNG:
    def test_random_bytes_length(self):
        for n in [1, 16, 32, 64]:
            assert len(random_bytes(n)) == n

    def test_random_bytes_not_constant(self):
        a = random_bytes(32)
        b = random_bytes(32)
        assert a != b  # overwhelming probability

    def test_random_block(self):
        b = random_block()
        assert isinstance(b, Block)
        assert len(b.data) == BLOCK_SIZE

    def test_random_state_value(self):
        sv = random_state_value()
        assert isinstance(sv, StateValue)
        assert len(sv.data) == STATE_SIZE

    def test_random_id_default(self):
        rid = random_id()
        assert len(rid) == 16

    def test_random_id_custom_size(self):
        rid = random_id(32)
        assert len(rid) == 32


# ---------------------------------------------------------------------------
# Constant-Time Comparison
# ---------------------------------------------------------------------------

class TestConstantTimeEqual:
    def test_equal(self):
        assert constant_time_equal(b"abc", b"abc")

    def test_not_equal(self):
        assert not constant_time_equal(b"abc", b"xyz")

    def test_different_lengths(self):
        assert not constant_time_equal(b"ab", b"abc")

    def test_empty(self):
        assert constant_time_equal(b"", b"")


# ---------------------------------------------------------------------------
# encode_u64_be
# ---------------------------------------------------------------------------

class TestEncodeU64Be:
    def test_zero(self):
        assert encode_u64_be(0) == b"\x00" * 8

    def test_one(self):
        assert encode_u64_be(1) == b"\x00" * 7 + b"\x01"

    def test_known_value(self):
        assert encode_u64_be(42) == b"\x00\x00\x00\x00\x00\x00\x00\x2a"

    def test_max(self):
        result = encode_u64_be(2**64 - 1)
        assert result == b"\xff" * 8
        assert len(result) == 8
