# Running on a managed laptop (TLS interception, proxies)

## The symptom

```
SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: self-signed certificate in certificate chain'))
```

## What is actually happening

Your employer's security appliance terminates HTTPS, inspects it, and re-signs
it with an internal certificate authority. Browsers on the laptop accept this
because IT installed that CA in the Windows certificate store. Python does not
look there - it ships its own list (`certifi`) - so every outbound call fails.

Nothing is broken and nothing is misconfigured in this tool. It is a trust-store
gap, and it affects **everything** the tool does over the network: DuckDuckGo,
fetching SAP pages for T-code verification, OpenAlex, and your own Confluence.

## Diagnosis

```bash
python -m afsgap doctor
```

It reports which trust source is in use, whether a proxy is set, and whether a
real request succeeds - and prints the remedies if the CA is the problem.

## Fix 1 - use the machine's certificate store (recommended)

```bash
pip install truststore
```

That is the whole fix. `truststore` routes Python's TLS verification through the
Windows (or macOS) certificate store, where your corporate CA already lives.
afsgap installs it at startup automatically when the package is present, so the
Anthropic SDK and the search client benefit too, not just afsgap's own requests.

It is in `requirements.txt`, so a normal install already has it. Re-run
`doctor`: the TLS line should read *operating system certificate store*.

## Fix 2 - export the certificates to a PEM bundle

If `truststore` cannot be installed, or something in the chain still refuses:

```bash
python -m afsgap export-ca-bundle
```

This reads the machine's ROOT and CA certificate stores, merges them with the
defaults, and writes `corp-ca-bundle.pem`. Put the printed path in `.env`:

```ini
AFSGAP_CA_BUNDLE=C:\Users\you\aftersales-gap-analyzer\corp-ca-bundle.pem
```

Every component uses it, including the DuckDuckGo client, which has its own HTTP
stack and has to be told separately.

*(Export reads the Windows certificate store. On macOS or Linux, use fix 1, or
ask IT for the root CA as a `.pem` and point `AFSGAP_CA_BUNDLE` at it.)*

## Fix 3 - ask IT for the certificate

Request "the corporate root CA in PEM format for use with Python". Most IT
desks have this ready; it is a routine request from anyone running Python,
Node or Java on a managed machine. Then set `AFSGAP_CA_BUNDLE` as above.

## The escape hatch, and why it is last

```ini
AFSGAP_INSECURE_TLS=true
```

This disables certificate verification. It works immediately, it logs a warning
every run, and it means nothing distinguishes your proxy from anyone else's -
the tool will happily talk to whatever answers. Use it to prove the diagnosis on
a throwaway test, not for a run whose output anyone will rely on, and never with
Confluence credentials in `.env`.

## Proxies

`HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` are honoured automatically. If your
laptop is already configured for the corporate proxy, nothing more is needed.
`doctor` prints the proxy it can see, which is a quick way to confirm the
environment variables reached Python.

If the proxy needs authentication you will see `407 Proxy Authentication
Required` rather than a certificate error - a different problem with a different
owner. Include the credentials in the URL
(`http://user:pass@proxy:8080`) only if your policy allows it.

## Sites your network may block outright

Search engines and AI-related domains are sometimes blocked by policy rather
than by TLS. If `doctor` shows TLS as OK but search returns nothing:

- Try `--search none` with `AFSGAP_OPENALEX_ENABLED=false` to confirm the rest of
  the pipeline works; the run completes and states that research found nothing.
- Ask whether `duckduckgo.com` is allowlisted. It often is not, while
  `help.sap.com` is.
- The stage cache means nothing already retrieved is lost while you sort this
  out - delete individual files under `.cache/stages/` to redo just one stage.

## What none of this changes

Your Confluence content and the analysis still stay on the laptop in local mode.
Fixing TLS makes outbound *search* work; it does not send anything of yours
anywhere it was not already going.
