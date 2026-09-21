"""Corporate TLS interception: detection, remedies, and honest degradation."""

import ssl

import pytest
import requests

from afsgap.config import Settings
from afsgap.net import (
    build_session,
    export_ca_bundle,
    is_tls_trust_error,
    resolve_verify,
)
from afsgap.research.http import fetch_text
from afsgap.search.duckduckgo import DuckDuckGoBackend
from afsgap.sources.confluence import ConfluenceClient, ConfluenceUnavailableError

# The exact message a Windows laptop behind a TLS-inspecting proxy produces.
REAL_ERROR = (
    "HTTPSConnectionPool(host='html.duckduckgo.com', port=443): Max retries exceeded with url: "
    "/html/ (Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] "
    "certificate verify failed: self-signed certificate in certificate chain (_ssl.c:1082)')))"
)


# -- detection --------------------------------------------------------------
def test_the_real_world_error_is_recognised():
    assert is_tls_trust_error(requests.exceptions.SSLError(REAL_ERROR))


@pytest.mark.parametrize(
    "message",
    [
        "certificate verify failed: unable to get local issuer certificate",
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self signed certificate",
        "SSLCertVerificationError: self-signed certificate in certificate chain",
    ],
)
def test_other_trust_failures_are_recognised(message):
    assert is_tls_trust_error(Exception(message))


@pytest.mark.parametrize(
    "message",
    [
        "Max retries exceeded: Connection refused",
        "Read timed out",
        "407 Proxy Authentication Required",
        "No results found.",
    ],
)
def test_unrelated_failures_are_not_blamed_on_tls(message):
    assert not is_tls_trust_error(Exception(message))


# -- verify resolution ------------------------------------------------------
def test_default_verification_is_on():
    assert resolve_verify(Settings(use_system_trust=False)) is True


def test_explicit_bundle_is_used(tmp_path):
    bundle = tmp_path / "corp.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    assert resolve_verify(Settings(ca_bundle=str(bundle))) == str(bundle)


def test_missing_bundle_fails_loudly_rather_than_silently_trusting(tmp_path):
    with pytest.raises(FileNotFoundError, match="export-ca-bundle"):
        resolve_verify(Settings(ca_bundle=str(tmp_path / "nope.pem")))


def test_insecure_mode_is_opt_in_and_warns(caplog):
    with caplog.at_level("WARNING"):
        assert resolve_verify(Settings(insecure_tls=True)) is False
    assert "DISABLED" in caplog.text


def test_session_carries_the_trust_settings(tmp_path):
    bundle = tmp_path / "corp.pem"
    bundle.write_text("x", encoding="utf-8")
    session = build_session(Settings(ca_bundle=str(bundle)))
    assert session.verify == str(bundle)
    assert "afsgap" in session.headers["User-Agent"]


# -- CA export --------------------------------------------------------------
def test_export_explains_itself_when_unsupported(tmp_path, monkeypatch):
    monkeypatch.delattr(ssl, "enum_certificates", raising=False)
    with pytest.raises(RuntimeError, match="truststore"):
        export_ca_bundle(tmp_path / "out.pem")


def test_export_writes_system_certificates(tmp_path, monkeypatch):
    der = ssl.PEM_cert_to_DER_cert(
        "-----BEGIN CERTIFICATE-----\n"
        "MIIBFzCBvqADAgECAgEBMAoGCCqGSM49BAMCMA8xDTALBgNVBAMMBHRlc3QwHhcN\n"
        "MjAwMTAxMDAwMDAwWhcNMzAwMTAxMDAwMDAwWjAPMQ0wCwYDVQQDDAR0ZXN0MFkw\n"
        "EwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE2x8kEfMhFB0wR2sBRRl6mJbBnCK9AJs5\n"
        "-----END CERTIFICATE-----\n"
    )
    monkeypatch.setattr(ssl, "enum_certificates", lambda store: [(der, "x509_asn", True)], raising=False)
    path, added = export_ca_bundle(tmp_path / "corp-ca-bundle.pem")
    assert path.exists() and added == 2          # ROOT + CA stores
    assert "BEGIN CERTIFICATE" in path.read_text(encoding="utf-8")


# -- degradation ------------------------------------------------------------
def test_page_fetch_points_at_the_fix_instead_of_dumping_a_stack(tmp_path, caplog, monkeypatch):
    class Failing:
        def get(self, *a, **k):
            raise requests.exceptions.SSLError(REAL_ERROR)

    with caplog.at_level("WARNING"):
        text = fetch_text("https://help.sap.com/docs/x", tmp_path, session=Failing())
    assert text == ""                             # the run continues
    assert "afsgap doctor" in caplog.text


def test_confluence_translates_the_tls_error(monkeypatch):
    client = ConfluenceClient(Settings(
        confluence_base_url="https://confluence.example.com",
        confluence_api_token="pat",
        confluence_auth="bearer",
    ))

    def explode(*args, **kwargs):
        raise requests.exceptions.SSLError(REAL_ERROR)

    monkeypatch.setattr(client.session, "get", explode)
    with pytest.raises(ConfluenceUnavailableError, match="afsgap doctor"):
        client._get("/rest/api/content/1", {})


def test_search_reports_tls_once_and_clearly(caplog, monkeypatch):
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    backend._ddgs = None

    def explode(*args, **kwargs):
        raise requests.exceptions.SSLError(REAL_ERROR)

    monkeypatch.setattr(backend.session, "post", explode)
    with caplog.at_level("ERROR"):
        assert backend.search("SAP returns") == []
    assert "afsgap doctor" in caplog.text


# -- the steering fix -------------------------------------------------------
def test_site_steering_avoids_login_walled_domains(monkeypatch):
    """support.sap.com is on the allowlist but is not worth a site: query."""
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    seen: list[str] = []
    monkeypatch.setattr(backend, "_one", lambda query, max_results: seen.append(query) or [])

    from afsgap.filters.sources import SourceClassifier

    backend.search("SAP EWM returns inbound delivery process", allowed_domains=SourceClassifier().sap_official)

    steered = [query for query in seen if "site:" in query]
    assert steered, "should still steer at something"
    assert all("support.sap.com" not in query for query in steered), seen
    assert all("launchpad" not in query for query in steered), seen
    assert any("help.sap.com" in query for query in steered)


def test_bare_query_always_runs_too(monkeypatch):
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    seen: list[str] = []
    monkeypatch.setattr(backend, "_one", lambda query, max_results: seen.append(query) or [])
    backend.search("returns process", allowed_domains=["help.sap.com"])
    assert seen[-1] == "returns process"


def test_steering_falls_back_when_no_priority_domain_applies(monkeypatch):
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    seen: list[str] = []
    monkeypatch.setattr(backend, "_one", lambda query, max_results: seen.append(query) or [])
    backend.search("practice", allowed_domains=["mckinsey.com"])
    assert any("site:mckinsey.com" in query for query in seen)
