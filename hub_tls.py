"""
TLS setup for the hub.

Configured via environment variables (see .env.example):

  TLS_MODE=off          Plain HTTP (default).
  TLS_MODE=self-signed  Generate a private CA plus a server certificate signed
                        by it, stored in TLS_CERT_DIR (default ./certs). The CA
                        is reused across restarts; the server certificate is
                        re-issued when it nears expiry or its hostnames change.
                        Copy certs/ca.pem to each sync agent machine.
  TLS_MODE=custom       Serve an externally managed certificate and key from
                        TLS_CERT_FILE and TLS_KEY_FILE (PEM).

  TLS_HOSTNAMES         Extra DNS names / IP addresses the self-signed server
                        certificate should be valid for (comma-separated), e.g.
                        "hub.lan,192.168.1.10". localhost, 127.0.0.1, ::1 and
                        this machine's hostname are always included.

When TLS is enabled the hub listens on HTTPS only.

Run `python hub_tls.py` to generate / refresh the self-signed certificates and
print the CA fingerprint without starting the hub.
"""

import ipaddress
import os
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODES = ("off", "self-signed", "custom")

CA_VALID_DAYS = 3650
# Browsers reject leaf certificates valid for more than 398 days.
SERVER_VALID_DAYS = 397
RENEW_BEFORE_DAYS = 30


class TLSConfigError(Exception):
    pass


def tls_mode():
    mode = os.environ.get("TLS_MODE", "off").strip().lower() or "off"
    if mode not in MODES:
        raise TLSConfigError(f"TLS_MODE must be one of {', '.join(MODES)} (got '{mode}')")
    return mode


def _default_cert_dir():
    return Path(__file__).parent / "certs"


def _hostnames():
    names = ["localhost", "127.0.0.1", "::1"]
    try:
        host = socket.gethostname()
        if host:
            names.append(host)
    except OSError:
        pass
    extra = os.environ.get("TLS_HOSTNAMES", "")
    names += [n.strip() for n in extra.split(",") if n.strip()]
    # De-duplicate case-insensitively, preserving order.
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _san_entries(names):
    from cryptography import x509
    entries = []
    for n in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            entries.append(x509.DNSName(n.lower()))
    return entries


def _write_private(path, data):
    """Write data to path with 0600 permissions from the moment it is created."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _new_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def _key_pem(key):
    from cryptography.hazmat.primitives import serialization
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _load_key(path):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def _load_cert(path):
    from cryptography import x509
    return x509.load_pem_x509_certificate(Path(path).read_bytes())


def _create_ca(ca_cert_path, ca_key_path):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization

    key = _new_key()
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Claude Usage Tracker"),
        x509.NameAttribute(NameOID.COMMON_NAME,
                           f"Claude Usage Tracker Local CA ({socket.gethostname()})"),
    ])
    now = datetime.now(timezone.utc)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CA_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(ski, critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski),
                       critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private(ca_key_path, _key_pem(key))
    Path(ca_cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key


def _create_server_cert(cert_path, key_path, ca_cert, ca_key, names):
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization

    key = _new_key()
    now = datetime.now(timezone.utc)
    ca_ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=SERVER_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(_san_entries(names)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_private(key_path, _key_pem(key))
    Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _server_cert_needs_renewal(cert_path, key_path, ca_cert, names):
    from cryptography import x509
    if not (Path(cert_path).exists() and Path(key_path).exists()):
        return "missing"
    try:
        cert = _load_cert(cert_path)
    except Exception:
        return "unreadable"
    try:
        # Raises if the issuer name doesn't match the CA or the signature is bad.
        cert.verify_directly_issued_by(ca_cert)
    except Exception:
        return "not signed by the current CA"
    if cert.not_valid_after_utc - datetime.now(timezone.utc) < timedelta(days=RENEW_BEFORE_DAYS):
        return "expiring"
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return "no subjectAltName"
    if set(san) != set(_san_entries(names)):
        return "hostnames changed"
    return None


def ensure_self_signed(cert_dir=None, log=print):
    """
    Create the private CA (once) and a current server certificate.
    Returns (server_cert_path, server_key_path, ca_cert_path).
    """
    cert_dir = Path(cert_dir or os.environ.get("TLS_CERT_DIR") or _default_cert_dir())
    cert_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cert_dir, 0o700)

    ca_cert_path = cert_dir / "ca.pem"
    ca_key_path = cert_dir / "ca-key.pem"
    cert_path = cert_dir / "server.pem"
    key_path = cert_dir / "server-key.pem"

    if ca_cert_path.exists() and ca_key_path.exists():
        ca_cert, ca_key = _load_cert(ca_cert_path), _load_key(ca_key_path)
    else:
        log(f"TLS: creating private CA in {cert_dir}")
        ca_cert, ca_key = _create_ca(ca_cert_path, ca_key_path)

    names = _hostnames()
    reason = _server_cert_needs_renewal(cert_path, key_path, ca_cert, names)
    if reason:
        log(f"TLS: issuing server certificate ({reason}) for: {', '.join(names)}")
        _create_server_cert(cert_path, key_path, ca_cert, ca_key, names)

    return str(cert_path), str(key_path), str(ca_cert_path)


def ca_fingerprint(ca_cert_path):
    from cryptography.hazmat.primitives import hashes
    return _load_cert(ca_cert_path).fingerprint(hashes.SHA256()).hex(":").upper()


def _server_context(cert_file, key_file):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(cert_file, key_file)
    except (OSError, ssl.SSLError) as e:
        raise TLSConfigError(f"could not load certificate/key ({cert_file}, {key_file}): {e}")
    return ctx


def server_ssl_context(log=print):
    """
    Return an ssl.SSLContext for the configured TLS_MODE, or None for plain HTTP.
    Raises TLSConfigError on misconfiguration so the hub never silently falls
    back to HTTP when TLS was requested.
    """
    mode = tls_mode()
    if mode == "off":
        return None

    if mode == "custom":
        cert_file = os.environ.get("TLS_CERT_FILE", "").strip()
        key_file = os.environ.get("TLS_KEY_FILE", "").strip()
        if not cert_file or not key_file:
            raise TLSConfigError("TLS_MODE=custom requires TLS_CERT_FILE and TLS_KEY_FILE")
        log(f"TLS: serving certificate {cert_file}")
        return _server_context(cert_file, key_file)

    cert_file, key_file, ca_file = ensure_self_signed(log=log)
    log(f"TLS: CA certificate for sync agents: {ca_file}")
    log(f"TLS: CA SHA-256 fingerprint: {ca_fingerprint(ca_file)}")
    return _server_context(cert_file, key_file)


if __name__ == "__main__":
    cert, key, ca = ensure_self_signed()
    print(f"Server certificate: {cert}")
    print(f"Server key:         {key}")
    print(f"CA certificate:     {ca}")
    print(f"CA SHA-256:         {ca_fingerprint(ca)}")
