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

## When search is blocked but the sites are not

This is the common case in a corporate estate: the web filter blocks search
engines by category, while `help.sap.com` is allowlisted because people need it
for work. `doctor` tests each search backend separately, so you can see which:

```
Search backend     : duckduckgo,mojeek,seeds
  duckduckgo       : no results - blocked or rate-limited
  mojeek           : OK (3 result(s), e.g. https://help.sap.com/docs/...)
  seeds            : no seed URLs yet - add them to data/seed_sources.yaml
```

Options, in order of effort:

**1. Chain a second engine.** Corporate filters usually block the big engines by
name. Mojeek has its own index and is often reachable:

```ini
AFSGAP_SEARCH=duckduckgo,mojeek,seeds
```

The chain tries each in order and uses the first that returns anything.

**2. Use a SearXNG instance.** If your organisation runs one, or you run one in
Docker locally, it is the most reliable option - inside the perimeter, no API
key, JSON out:

```ini
AFSGAP_SEARCH=searxng,seeds
AFSGAP_SEARXNG_URL=http://localhost:8080
```

**3. Supply the URLs yourself (`seeds`).** No search engine involved. You find
the pages in your browser - which works on your laptop even when Python's search
does not - and paste them into `data/seed_sources.yaml`:

```yaml
sources:
  - url: https://help.sap.com/docs/<the page you opened>
    title: Returns Processing
    topics: [returns, defective, parts]
```

The pipeline then fetches, filters and extracts from them exactly as it would
from search results, and T-codes are still verified against the literal page
text. Seeds appear in the evidence register like any other source.

Ten well-chosen pages produce a better document than fifty search results, so
this is not a degraded mode - it is just more work for you. The file ships empty
on purpose: nothing is guessed on your behalf.

**4. Confirm the rest works.** `--search none` with
`AFSGAP_OPENALEX_ENABLED=false` runs the pipeline with no outbound search at
all; it completes and states plainly that research returned nothing.

The stage cache means nothing already retrieved is lost while you work through
this - delete individual files under `.cache/stages/` to redo just one stage.

## What none of this changes

Your Confluence content and the analysis still stay on the laptop in local mode.
Fixing TLS makes outbound *search* work; it does not send anything of yours
anywhere it was not already going.
