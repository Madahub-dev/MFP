"""Unit tests for mfp/core/encoding.py (I-05)."""

import pytest

from mfp.core.encoding import (
    build_aad,
    build_encoding_context,
    decode,
    derive_encoding_key,
    derive_nonce,
    encode,
)
from mfp.core.primitives import random_id, random_state_value
from mfp.core.types import (
    ALGORITHM_AES_256_GCM,
    ChannelId,
    EncodingContext,
    StateValue,
)


# ---------------------------------------------------------------------------
# derive_encoding_key()
# ---------------------------------------------------------------------------

class TestDeriveEncodingKey:
    def test_returns_state_value(self):
        result = derive_encoding_key(random_state_value(), ALGORITHM_AES_256_GCM)
        assert isinstance(result, StateValue)

    def test_deterministic(self):
        sl = StateValue(b"\x42" * 32)
        a = derive_encoding_key(sl, ALGORITHM_AES_256_GCM)
        b = derive_encoding_key(sl, ALGORITHM_AES_256_GCM)
        assert a == b

    def test_different_state_different_key(self):
        a = derive_encoding_key(StateValue(b"\x01" * 32), ALGORITHM_AES_256_GCM)
        b = derive_encoding_key(StateValue(b"\x02" * 32), ALGORITHM_AES_256_GCM)
        assert a != b

    def test_different_algorithm_different_key(self):
        sl = StateValue(b"\x42" * 32)
        a = derive_encoding_key(sl, b"aes-256-gcm")
        b = derive_encoding_key(sl, b"chacha20-poly1305")
        assert a != b


# ---------------------------------------------------------------------------
# derive_nonce()
# ---------------------------------------------------------------------------

class TestDeriveNonce:
    def test_length(self):
        nonce = derive_nonce(random_state_value(), ChannelId(random_id(16)), 0)
        assert len(nonce) == 12

    def test_deterministic(self):
        key = StateValue(b"\x42" * 32)
        ch = ChannelId(b"\x01" * 16)
        a = derive_nonce(key, ch, 0)
        b = derive_nonce(key, ch, 0)
        assert a == b

    def test_different_step(self):
        key = StateValue(b"\x42" * 32)
        ch = ChannelId(b"\x01" * 16)
        a = derive_nonce(key, ch, 0)
        b = derive_nonce(key, ch, 1)
        assert a != b

    def test_different_channel(self):
        key = StateValue(b"\x42" * 32)
        a = derive_nonce(key, ChannelId(b"\x01" * 16), 0)
        b = derive_nonce(key, ChannelId(b"\x02" * 16), 0)
        assert a != b


# ---------------------------------------------------------------------------
# build_aad()
# ---------------------------------------------------------------------------

class TestBuildAad:
    def test_length(self):
        aad = build_aad(ChannelId(b"\x00" * 16), 0)
        assert len(aad) == 24  # 16 (channel_id) + 8 (u64)

    def test_deterministic(self):
        ch = ChannelId(b"\x42" * 16)
        a = build_aad(ch, 5)
        b = build_aad(ch, 5)
        assert a == b

    def test_incorporates_channel(self):
        a = build_aad(ChannelId(b"\x01" * 16), 0)
        b = build_aad(ChannelId(b"\x02" * 16), 0)
        assert a != b

    def test_incorporates_step(self):
        ch = ChannelId(b"\x42" * 16)
        a = build_aad(ch, 0)
        b = build_aad(ch, 1)
        assert a != b


# ---------------------------------------------------------------------------
# build_encoding_context()
# ---------------------------------------------------------------------------

class TestBuildEncodingContext:
    def test_returns_encoding_context(self):
        ctx = build_encoding_context(random_state_value(), ChannelId(random_id(16)), 0)
        assert isinstance(ctx, EncodingContext)

    def test_default_algorithm(self):
        ctx = build_encoding_context(random_state_value(), ChannelId(random_id(16)), 0)
        assert ctx.algorithm_id == ALGORITHM_AES_256_GCM

    def test_key_derived(self):
        sl = random_state_value()
        ch = ChannelId(random_id(16))
        ctx = build_encoding_context(sl, ch, 0)
        expected_key = derive_encoding_key(sl, ALGORITHM_AES_256_GCM)
        assert ctx.key == expected_key

    def test_fields_set(self):
        sl = random_state_value()
        ch = ChannelId(b"\x42" * 16)
        ctx = build_encoding_context(sl, ch, 7)
        assert ctx.channel_id == ch
        assert ctx.step == 7


# ---------------------------------------------------------------------------
# encode() / decode()
# ---------------------------------------------------------------------------

class TestEncodeDecode:
    def _make_ctx(self):
        return build_encoding_context(
            random_state_value(), ChannelId(random_id(16)), 0,
        )

    def test_roundtrip(self):
        ctx = self._make_ctx()
        payload = b"Hello, world!"
        encoded = encode(payload, ctx)
        decoded = decode(encoded, ctx)
        assert decoded == payload

    def test_ciphertext_longer_by_16(self):
        ctx = self._make_ctx()
        payload = b"x" * 100
        encoded = encode(payload, ctx)
        assert len(encoded) == len(payload) + 16

    def test_empty_payload(self):
        ctx = self._make_ctx()
        encoded = encode(b"", ctx)
        decoded = decode(encoded, ctx)
        assert decoded == b""

    def test_large_payload(self):
        ctx = self._make_ctx()
        payload = b"A" * 10_000
        encoded = encode(payload, ctx)
        decoded = decode(encoded, ctx)
        assert decoded == payload

    def test_tampered_returns_none(self):
        ctx = self._make_ctx()
        encoded = encode(b"secret", ctx)
        tampered = encoded[:-1] + bytes([encoded[-1] ^ 0xFF])
        assert decode(tampered, ctx) is None

    def test_wrong_step_returns_none(self):
        sl = random_state_value()
        ch = ChannelId(random_id(16))
        ctx0 = build_encoding_context(sl, ch, 0)
        ctx1 = build_encoding_context(sl, ch, 1)
        encoded = encode(b"payload", ctx0)
        assert decode(encoded, ctx1) is None

    def test_wrong_channel_returns_none(self):
        sl = random_state_value()
        ctx_a = build_encoding_context(sl, ChannelId(random_id(16)), 0)
        ctx_b = build_encoding_context(sl, ChannelId(random_id(16)), 0)
        encoded = encode(b"payload", ctx_a)
        assert decode(encoded, ctx_b) is None

    def test_wrong_key_returns_none(self):
        ch = ChannelId(random_id(16))
        ctx_a = build_encoding_context(random_state_value(), ch, 0)
        ctx_b = build_encoding_context(random_state_value(), ch, 0)
        encoded = encode(b"payload", ctx_a)
        assert decode(encoded, ctx_b) is None

    def test_unsupported_algorithm_encode(self):
        ctx = EncodingContext(
            algorithm_id=b"unknown",
            key=random_state_value(),
            channel_id=ChannelId(random_id(16)),
            step=0,
        )
        with pytest.raises(ValueError, match="Unsupported"):
            encode(b"payload", ctx)

    def test_unsupported_algorithm_decode(self):
        ctx = EncodingContext(
            algorithm_id=b"unknown",
            key=random_state_value(),
            channel_id=ChannelId(random_id(16)),
            step=0,
        )
        with pytest.raises(ValueError, match="Unsupported"):
            decode(b"ciphertext", ctx)

    def test_key_rotation_via_state_advancement(self):
        """Different Sl produces different key → old ciphertext cannot decode."""
        ch = ChannelId(random_id(16))
        sl0 = random_state_value()
        sl1 = random_state_value()  # simulates advanced state
        ctx0 = build_encoding_context(sl0, ch, 0)
        ctx1 = build_encoding_context(sl1, ch, 0)
        encoded = encode(b"payload", ctx0)
        assert decode(encoded, ctx1) is None
