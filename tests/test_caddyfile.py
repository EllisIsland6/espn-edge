"""The origin Caddyfile, pinned where it bit.

The first web task died in Caddy before it had a logger: `cert_issuer zerossl`
in the global block is, in current Caddy, the ZeroSSL *API* issuer and demands
an API key, so adaptation failed with "wrong argument count or unexpected line
ending after 'zerossl'" and the container exited 1 with nothing in CloudWatch.
Measured with `caddy adapt` against the file. With an email set, Caddy's
defaults are Let's Encrypt with ZeroSSL ACME as the fallback, which is what
amendment C3 asks for, so the file says nothing about issuers at all.
"""

from __future__ import annotations

import re
from pathlib import Path

CADDYFILE = Path(__file__).resolve().parents[1] / "ops" / "caddy" / "Caddyfile"


def _directives() -> list[str]:
    out = []
    for line in CADDYFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


def test_no_cert_issuer_directive():
    assert not [d for d in _directives() if d.startswith("cert_issuer")], "issuers are Caddy's defaults; see the module docstring"


def test_the_global_block_sets_the_acme_email():
    assert "email {$ACME_EMAIL}" in _directives()


def test_the_origin_uses_dns01_via_route53_and_refuses_without_the_header():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert re.search(r"tls \{\s*dns route53 \{", text)
    assert "@not_cloudfront not header X-Origin-Verify {$ORIGIN_VERIFY}" in text
    assert "respond @not_cloudfront 403" in text
    assert "reverse_proxy app:8000" in text
