"""Outbound HTTP, with corporate TLS interception handled in one place.

On a managed laptop, HTTPS is usually re-signed by a security appliance. Python
does not trust that appliance's CA out of the box, so every request fails with::

    CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain

That is a trust-store problem, not a bug in the site being fetched. There are
three honest ways out, in order of preference:

1. **System trust store** - the corporate CA is already in the Windows/macOS
   store because IT put it there. The ``truststore`` package makes Python use
   that store, so nothing has to be exported. Default when installed.
2. **An explicit CA bundle** - export the corporate root once
   (``python -m afsgap export-ca-bundle``) and point ``AFSGAP_CA_BUNDLE`` at it.
3. **Disabling verification** - ``AFSGAP_INSECURE_TLS=true``. It works, it is
   loud about itself, and it is not something to leave switched on: with
   verification off, nothing distinguishes your proxy from anyone else's.
"""

from __future__ import annotations

import logging
import ssl
from pathlib import Path

import requests

from .config import Settings

logger = logging.getLogger(__name__)

USER_AGENT = "afsgap-process-research/0.1 (+internal SAP S/4HANA design study)"

_SYSTEM_TRUST_INSTALLED = False

TLS_HELP = """Corporate TLS interception is blocking this request.

Your network re-signs HTTPS traffic with an internal certificate authority that
Python does not trust yet. Pick one:

  1. Use the machine's own certificate store (easiest, nothing to export):
         pip install truststore
     afsgap uses it automatically once installed.

  2. Export the corporate root certificates and point afsgap at them:
         python -m afsgap export-ca-bundle
     then put the printed path in .env as AFSGAP_CA_BUNDLE=...

  3. Ask IT for the corporate root CA as a .pem and set AFSGAP_CA_BUNDLE to it.

Last resort, and only for a throwaway test: AFSGAP_INSECURE_TLS=true disables
certificate checking entirely."""


def is_tls_trust_error(exc: BaseException) -> bool:
    """True when an exception is the corporate-CA problem rather than a real fault."""
    text = str(exc).lower()
    markers = (
        "certificate_verify_failed",
        "self-signed certificate",
        "self signed certificate",
        "unable to get local issuer certificate",
        "certificate verify failed",
    )
    return any(marker in text for marker in markers)


def install_system_trust(settings: Settings) -> bool:
    """Route Python's TLS verification through the OS certificate store."""
    global _SYSTEM_TRUST_INSTALLED
    if _SYSTEM_TRUST_INSTALLED or not settings.use_system_trust:
        return _SYSTEM_TRUST_INSTALLED
    if settings.ca_bundle or settings.insecure_tls:
        return False          # an explicit choice wins over the system store
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    _SYSTEM_TRUST_INSTALLED = True
    logger.debug("Using the operating system certificate store for TLS verification.")
    return True


def resolve_verify(settings: Settings) -> bool | str:
    """What to pass as ``verify=`` - a CA bundle path, or True/False."""
    if settings.insecure_tls:
        logger.warning(
            "TLS certificate verification is DISABLED (AFSGAP_INSECURE_TLS). Traffic can be "
            "read and altered in transit - use this for a throwaway test only."
        )
        return False
    if settings.ca_bundle:
        path = Path(settings.ca_bundle).expanduser()
        if not path.exists():
            raise FileNotFoundError(
                f"AFSGAP_CA_BUNDLE points at {path}, which does not exist. "
                "Run `python -m afsgap export-ca-bundle` to create one."
            )
        return str(path)
    install_system_trust(settings)
    return True


def build_session(settings: Settings) -> requests.Session:
    """A session with the right trust settings and a polite user agent.

    Proxy settings are picked up from the standard ``HTTPS_PROXY`` /
    ``HTTP_PROXY`` / ``NO_PROXY`` environment variables by requests itself, so a
    laptop already configured for the corporate proxy needs nothing extra here.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.verify = resolve_verify(settings)
    return session


# ---------------------------------------------------------------------------
# CA bundle export
# ---------------------------------------------------------------------------
def export_ca_bundle(destination: Path) -> tuple[Path, int]:
    """Write a PEM bundle of the machine's root certificates plus certifi's.

    Uses ``ssl.enum_certificates``, which reads the Windows certificate store -
    where a managed laptop's corporate root already lives. The result is a file
    that requests, ddgs and anything else taking a CA bundle can use.
    """
    pem_blocks: list[str] = []

    try:
        import certifi

        pem_blocks.append(Path(certifi.where()).read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - certifi ships with requests
        logger.warning("Could not read the certifi bundle: %s", exc)

    enum_certificates = getattr(ssl, "enum_certificates", None)
    if enum_certificates is None:
        raise RuntimeError(
            "Reading the system certificate store is only supported on Windows. "
            "On macOS or Linux, install `truststore` (pip install truststore), or ask IT "
            "for the corporate root CA as a .pem file and set AFSGAP_CA_BUNDLE to it."
        )

    added = 0
    for store in ("ROOT", "CA"):
        try:
            certificates = enum_certificates(store)
        except Exception as exc:
            logger.warning("Could not read the %s certificate store: %s", store, exc)
            continue
        for der, encoding, trust in certificates:
            if encoding != "x509_asn":
                continue
            # `trust` is True for "all purposes", or a set of OIDs; skip only
            # certificates explicitly not trusted for server authentication.
            if trust is not True and isinstance(trust, (set, frozenset)):
                if "1.3.6.1.5.5.7.3.1" not in trust:
                    continue
            try:
                pem_blocks.append(ssl.DER_cert_to_PEM_cert(der))
                added += 1
            except Exception:
                continue

    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(pem_blocks), encoding="utf-8")
    return destination, added
