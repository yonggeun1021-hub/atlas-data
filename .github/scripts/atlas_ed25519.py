"""Pure-Python Ed25519 (RFC 8032) — verification and signing, no dependencies.

Used instead of HMAC because the approval boundary requires ASYMMETRY: the
delivery job must be able to VERIFY an approval without holding anything that
would let it MINT one.  A shared HMAC key cannot do that -- give the delivery
job the key and it can forge approvals; withhold it and it cannot verify.

Adding `cryptography` to requirements-ci.txt would touch the CI contract, so
this is the RFC 8032 reference construction inline.  Correctness is pinned to
the official RFC 8032 §7.1 test vectors in the test suite.

Performance is irrelevant here: one verification per delivery.
"""

from __future__ import annotations

import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, P - 2, P) % P
_I = pow(2, (P - 1) // 4, P)
_BY = 4 * pow(5, P - 2, P) % P
_BX = 0  # filled below


def _H(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * _I) % P
    if x % 2 != 0:
        x = P - x
    return x


_BX = _xrecover(_BY)
B = (_BX % P, _BY % P, 1, (_BX * _BY) % P)
IDENT = (0, 1, 1, 0)


def _add(pt1, pt2):
    x1, y1, z1, t1 = pt1
    x2, y2, z2, t2 = pt2
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = t1 * 2 * _D * t2 % P
    dd = z1 * 2 * z2 % P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _double(pt):
    x1, y1, z1, _ = pt
    a = x1 * x1 % P
    b = y1 * y1 % P
    c = 2 * z1 * z1 % P
    h = a + b
    e = h - (x1 + y1) * (x1 + y1) % P
    g = a - b
    f = c + g
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _scalarmult(pt, e: int):
    if e == 0:
        return IDENT
    q = _scalarmult(pt, e // 2)
    q = _double(q)
    return _add(q, pt) if e & 1 else q


def _encodepoint(pt) -> bytes:
    x, y, z, _ = pt
    zi = _inv(z)
    x, y = x * zi % P, y * zi % P
    return ((y & ~(1 << 255)) | ((x & 1) << 255)).to_bytes(32, "little")


def _decodepoint(s: bytes):
    if len(s) != 32:
        raise ValueError("bad point length")
    value = int.from_bytes(s, "little")
    y = value & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != (value >> 255) & 1:
        x = P - x
    pt = (x, y, 1, x * y % P)
    if not _isoncurve(pt):
        raise ValueError("point is not on the curve")
    return pt


def _isoncurve(pt) -> bool:
    x, y, z, t = pt
    return (
        z % P != 0
        and x * y % P == z * t % P
        and (y * y - x * x - z * z - _D * t * t) % P == 0
    )


def _secret_scalar(sk: bytes) -> tuple[int, bytes]:
    h = _H(sk)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def publickey(sk: bytes) -> bytes:
    if len(sk) != 32:
        raise ValueError("secret key must be 32 bytes")
    a, _ = _secret_scalar(sk)
    return _encodepoint(_scalarmult(B, a))


def sign(message: bytes, sk: bytes) -> bytes:
    a, prefix = _secret_scalar(sk)
    pk = _encodepoint(_scalarmult(B, a))
    r = int.from_bytes(_H(prefix + message), "little") % L
    rr = _encodepoint(_scalarmult(B, r))
    k = int.from_bytes(_H(rr + pk + message), "little") % L
    s = (r + k * a) % L
    return rr + s.to_bytes(32, "little")


def verify(signature: bytes, message: bytes, pk: bytes) -> bool:
    """Constant-ish time is irrelevant: everything here is public."""
    try:
        if len(signature) != 64 or len(pk) != 32:
            return False
        rr = _decodepoint(signature[:32])
        a = _decodepoint(pk)
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False
        k = int.from_bytes(_H(signature[:32] + pk + message), "little") % L
        left = _scalarmult(B, s)
        right = _add(rr, _scalarmult(a, k))
        return _encodepoint(left) == _encodepoint(right)
    except (ValueError, OverflowError):
        return False
