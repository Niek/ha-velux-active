"""Local Netcom pairing for VELUX ACTIVE gateways."""

from __future__ import annotations

import base64
import logging
import socket
import struct
import time
import uuid
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

_LOGGER = logging.getLogger(__name__)

FRAME_PING = 0x0000
FRAME_PONG = 0x0001
FRAME_CLOSE = 0x0008
FRAME_SECURE = 0x0200
FRAME_ECDH_REQUEST = 0x0201
FRAME_ECDH_RESPONSE = 0x0202
FRAME_END_TO_END_NONCE_REQUEST = 0x020A
FRAME_END_TO_END_NONCE_RESPONSE = 0x020B
FRAME_END_TO_END_CHALLENGE_REQUEST = 0x020C
FRAME_END_TO_END_CHALLENGE_RESPONSE = 0x020D
FRAME_END_TO_END_KEY_REQUEST = 0x020E
FRAME_END_TO_END_KEY_RESPONSE = 0x020F
NETCOM_PORT = 25050


class VeluxPairingError(Exception):
    """Raised when local gateway pairing fails."""


@dataclass(slots=True)
class SigningKey:
    """VELUX gateway signing key pair."""

    sign_key_id: str
    hash_sign_key: str


@dataclass(slots=True)
class _Frame:
    frame_type: int
    payload: bytes


class _NetcomSocket:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._socket.settimeout(timeout)
        self._buffer = bytearray()

    def close(self) -> None:
        self._socket.close()

    def send(self, frame: bytes) -> None:
        self._socket.sendall(frame)

    def recv_frame(self) -> _Frame:
        while len(self._buffer) < 4:
            self._recv_more()

        frame_type, size = struct.unpack_from("<HH", self._buffer)
        total = 4 + size
        while len(self._buffer) < total:
            self._recv_more()

        raw = bytes(self._buffer[:total])
        del self._buffer[:total]
        return _Frame(frame_type, raw[4:])

    def _recv_more(self) -> None:
        chunk = self._socket.recv(4096)
        if not chunk:
            raise VeluxPairingError("Connection closed by gateway")
        self._buffer.extend(chunk)


class _SecureContext:
    def __init__(self, shared_key: bytes) -> None:
        self._aead = ChaCha20Poly1305(shared_key)
        self._tx_nonce = bytearray(12)
        self._rx_nonce = bytearray(12)

    def encrypt_frame(self, frame: bytes) -> bytes:
        encrypted = self._aead.encrypt(bytes(self._tx_nonce), frame, None)
        _increment_nonce(self._tx_nonce)
        return _pack_frame(FRAME_SECURE, encrypted)

    def decrypt_payload(self, payload: bytes) -> bytes:
        decrypted = self._aead.decrypt(bytes(self._rx_nonce), payload, None)
        _increment_nonce(self._rx_nonce)
        return decrypted


def retrieve_signing_key(
    *,
    host: str,
    timeout: int = 30,
    socket_timeout: float = 10.0,
) -> SigningKey:
    """Wait for the local Netcom listener and retrieve a signing key."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    attempt = 0

    _LOGGER.debug(
        "Waiting for VELUX gateway pairing listener at %s:%d", host, NETCOM_PORT
    )

    while time.monotonic() < deadline:
        try:
            if _tcp_open(host, NETCOM_PORT):
                attempt += 1
                _LOGGER.debug(
                    "VELUX gateway pairing listener is available; starting attempt %d",
                    attempt,
                )
                return _request_signing_key(host, NETCOM_PORT, socket_timeout)
        except Exception as err:
            # Keep retrying until the pairing deadline.
            last_error = err
            _LOGGER.debug(
                "VELUX gateway pairing attempt %d failed: %s: %s",
                attempt,
                type(err).__name__,
                err,
                exc_info=True,
            )
        time.sleep(1)

    if last_error:
        detail = str(last_error) or type(last_error).__name__
        raise VeluxPairingError(detail) from last_error
    raise VeluxPairingError(f"Local Netcom listener did not appear on {host}")


def _request_signing_key(host: str, port: int, timeout: float) -> SigningKey:
    key_id = uuid.uuid4().bytes
    sock = _NetcomSocket(host, port, timeout)
    _LOGGER.debug("Connected to VELUX gateway pairing listener")
    try:
        secure = _perform_ecdh(sock)
        _LOGGER.debug("VELUX gateway ECDH handshake completed")
        gateway_key = _request_end_to_end_key(sock, secure, key_id)
        _LOGGER.debug("VELUX gateway accepted signing key request")
        _verify_end_to_end_key(sock, secure, key_id, gateway_key)
        _LOGGER.debug("VELUX gateway signing key challenge verified")
        _close_netcom(sock, secure)
    finally:
        sock.close()

    _LOGGER.debug("VELUX gateway pairing completed successfully")
    return SigningKey(_b64(key_id), _b64(gateway_key))


def _perform_ecdh(sock: _NetcomSocket) -> _SecureContext:
    sock.send(_pack_frame(FRAME_PING))
    pong = _expect_frame(sock, {FRAME_PONG}, "pong")
    if pong.payload:
        raise VeluxPairingError("Pong had unexpected payload")

    private_key = x25519.X25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    sock.send(_pack_frame(FRAME_ECDH_REQUEST, b"\x01" + public_key))
    response = _expect_frame(sock, {FRAME_ECDH_RESPONSE}, "ECDH response")

    gateway_public_key = response.payload
    if len(gateway_public_key) == 33 and gateway_public_key[0] in (0, 1):
        gateway_public_key = gateway_public_key[1:]
    if len(gateway_public_key) != 32:
        raise VeluxPairingError("ECDH response did not contain a 32-byte public key")

    peer = x25519.X25519PublicKey.from_public_bytes(gateway_public_key)
    secret = private_key.exchange(peer)
    digest = hashes.Hash(hashes.SHA512())
    digest.update(secret)
    return _SecureContext(digest.finalize()[:32])


def _request_end_to_end_key(
    sock: _NetcomSocket, secure: _SecureContext, key_id: bytes
) -> bytes:
    request = _pack_frame(FRAME_END_TO_END_KEY_REQUEST, b"\x00" + key_id)
    sock.send(secure.encrypt_frame(request))

    payload = _receive_secure_payload(
        sock, secure, FRAME_END_TO_END_KEY_RESPONSE, "key response"
    )
    if not payload:
        raise VeluxPairingError("Gateway returned an empty key response")

    result = payload[0]
    if result != 0:
        meanings = {1: "NOK", 2: "bad key id", 3: "key table full"}
        raise VeluxPairingError(
            f"Gateway rejected key request: {meanings.get(result, 'unknown')}"
        )
    if len(payload) != 33:
        raise VeluxPairingError("Gateway returned an unexpected key length")

    return payload[1:]


def _verify_end_to_end_key(
    sock: _NetcomSocket, secure: _SecureContext, key_id: bytes, gateway_key: bytes
) -> None:
    """Complete the gateway nonce/challenge check for the new signing key."""
    sock.send(secure.encrypt_frame(_pack_frame(FRAME_END_TO_END_NONCE_REQUEST, b"\x00")))
    nonce = _receive_secure_payload(
        sock, secure, FRAME_END_TO_END_NONCE_RESPONSE, "nonce response"
    )
    if not nonce:
        raise VeluxPairingError("Gateway returned an empty nonce")

    digest = hashes.Hash(hashes.SHA512())
    digest.update(key_id)
    digest.update(gateway_key)
    digest.update(nonce)
    challenge_hash = digest.finalize()

    request = _pack_frame(FRAME_END_TO_END_CHALLENGE_REQUEST, key_id + challenge_hash)
    sock.send(secure.encrypt_frame(request))
    response = _receive_secure_payload(
        sock, secure, FRAME_END_TO_END_CHALLENGE_RESPONSE, "challenge response"
    )
    if not response:
        raise VeluxPairingError("Gateway returned an empty challenge response")

    result = response[0]
    if result != 0:
        meanings = {1: "NOK", 2: "bad key id"}
        raise VeluxPairingError(
            f"Gateway rejected key challenge: {meanings.get(result, 'unknown')}"
        )


def _close_netcom(sock: _NetcomSocket, secure: _SecureContext) -> None:
    """Close the Netcom session the same way the Android coordinator does."""
    sock.send(secure.encrypt_frame(_pack_frame(FRAME_CLOSE)))


def _receive_secure_payload(
    sock: _NetcomSocket, secure: _SecureContext, expected_type: int, label: str
) -> bytes:
    """Receive and decrypt a secure Netcom response payload."""
    secure_response = _expect_frame(sock, {FRAME_SECURE}, f"secure {label}")
    decrypted = secure.decrypt_payload(secure_response.payload)
    if len(decrypted) < 4:
        raise VeluxPairingError(f"Decrypted {label} was too short")

    frame_type, size = struct.unpack_from("<HH", decrypted)
    payload = decrypted[4:]
    if len(payload) != size:
        raise VeluxPairingError(f"Decrypted {label} size mismatch")
    if frame_type != expected_type:
        raise VeluxPairingError(
            f"Gateway returned unexpected {label} frame 0x{frame_type:04x}"
        )
    return payload


def _expect_frame(sock: _NetcomSocket, expected_types: set[int], label: str) -> _Frame:
    while True:
        frame = sock.recv_frame()
        if frame.frame_type in expected_types:
            return frame
        if frame.frame_type == FRAME_PING:
            sock.send(_pack_frame(FRAME_PONG))
            continue
        raise VeluxPairingError(f"Expected {label}, got frame 0x{frame.frame_type:04x}")


def _pack_frame(frame_type: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HH", frame_type, len(payload)) + payload


def _increment_nonce(nonce: bytearray) -> None:
    for index in range(11, 3, -1):
        if nonce[index] < 0xFF:
            nonce[index] += 1
            return
        nonce[index] = 0
    nonce[:] = b"\x00" * 12


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")
