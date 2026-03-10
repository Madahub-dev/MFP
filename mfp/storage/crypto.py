"""MFP Storage Encryption — per-cell AES-256-GCM for at-rest columns.

Encrypts sensitive BLOB columns (local_state, ratchet_state, shared_prng_seed)
before writing to SQLite. Decrypts on read.

Maps to: impl/I-13_schema.md §6
"""

from __future__ import annotations

from mfp.core.primitives import aes_256_gcm_decrypt, aes_256_gcm_encrypt, hmac_sha256
from mfp.core.types import StateValue


def derive_storage_key(master_key: bytes, runtime_id: bytes) -> StateValue:
    """Derive the storage-wide encryption key from master key + runtime ID.

    storage_key = HMAC-SHA-256(master_key, "mfp-storage-key" || runtime_id)
    """
    return hmac_sha256(master_key, b"mfp-storage-key" + runtime_id)


def derive_cell_key(
    storage_key: StateValue,
    table_name: bytes,
    column_name: bytes,
    row_id: bytes,
) -> StateValue:
    """Derive a per-cell encryption key.

    cell_key = HMAC-SHA-256(storage_key, table || column || row_id)
    """
    return hmac_sha256(storage_key.data, table_name + column_name + row_id)


def derive_cell_nonce(
    cell_key: StateValue,
    row_id: bytes,
    column_name: bytes,
) -> bytes:
    """Derive a deterministic 12-byte nonce for a cell.

    nonce = HMAC-SHA-256(cell_key, row_id || column_name)[:12]
    """
    full = hmac_sha256(cell_key.data, row_id + column_name)
    return full.data[:12]


def encrypt_cell(
    storage_key: StateValue,
    table_name: bytes,
    column_name: bytes,
    row_id: bytes,
    plaintext: bytes,
) -> bytes:
    """Encrypt a cell value. Returns nonce || ciphertext || tag."""
    cell_key = derive_cell_key(storage_key, table_name, column_name, row_id)
    nonce = derive_cell_nonce(cell_key, row_id, column_name)
    aad = row_id + column_name
    ciphertext = aes_256_gcm_encrypt(cell_key.data, nonce, plaintext, aad)
    return nonce + ciphertext


def decrypt_cell(
    storage_key: StateValue,
    table_name: bytes,
    column_name: bytes,
    row_id: bytes,
    encrypted: bytes,
) -> bytes | None:
    """Decrypt a cell value. Returns None on integrity failure."""
    cell_key = derive_cell_key(storage_key, table_name, column_name, row_id)
    nonce = encrypted[:12]
    ciphertext = encrypted[12:]
    aad = row_id + column_name
    return aes_256_gcm_decrypt(cell_key.data, nonce, ciphertext, aad)
