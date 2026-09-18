# PyCharm setup

## 1. Open and configure the interpreter

1. **File -> Open** -> select the `aftersales-gap-analyzer` folder (open *this*
   folder, not the repository root, so the working directory is right).
2. **Settings -> Project -> Python Interpreter -> Add Interpreter -> Add Local ->
   Virtualenv**, base interpreter Python 3.11+, location `./.venv`.
3. In the PyCharm terminal:
   ```bash
   pip install -r requirements.txt
   ```
4. Mark the project root as Sources Root if imports show as unresolved:
   right-click the `aftersales-gap-analyzer` folder -> **Mark Directory as ->
   Sources Root**.

## 2. Credentials

```bash
cp .env.example .env
```

Put your Anthropic key and your Confluence settings in `.env` (see
`docs/CONFLUENCE.md` for Cloud versus Data Center). `afsgap.config` loads it via
`python-dotenv` at import, so no PyCharm environment-variable setup is needed.
`.env` is gitignored - keep it that way, and use a read-only Confluence token.

## 3. Run configurations

Create these under **Run -> Edit Configurations -> + -> Python**. For all of
them set **Working directory** to the project folder and check
*Module name* instead of *Script path*.

| Name | Module | Parameters |
| --- | --- | --- |
| Offline run (found) | `afsgap` | `run "Defective Parts Return" --offline --no-cache` |
| Offline run (greenfield) | `afsgap` | `run "Battery Pack Return" --offline --no-cache` |
| Confluence search | `afsgap` | `confluence-search "Defective Parts Return"` |
| Live run | `afsgap` | `run "Defective Parts Return" -v` |
| Live run (no industry) | `afsgap` | `run "Defective Parts Return" --skip-industry -v` |
| Filter check | `afsgap` | `check-filters "the delivery tolerance is checked at receipt"` |

Start with the two **Offline runs**: together they exercise the whole pipeline in
both modes, with no API cost and no Confluence connection.

Note the quotes around the process name - without them PyCharm passes only the
first word.

## 4. Tests

Set the default test runner to pytest (**Settings -> Tools -> Python Integrated
Tools -> Testing -> pytest**), then right-click `tests/` -> **Run 'pytest in
tests'**. The suite needs neither an API key nor network access.

## 5. Debugging the parts that usually need it

* **It picked the wrong Confluence page, or none** - run `confluence-search`
  first; it prints every candidate with its score and marks the one that would be
  used. Fix by setting `AFSGAP_CONFLUENCE_SPACES`, passing `--page-id`, or
  lowering `AFSGAP_CONFLUENCE_MIN_MATCH`. To re-read an edited page, delete its
  file under `.cache/confluence/`.
* **The transcribed AS-IS looks thin** - that is usually the page, not the
  extraction: the prompt is a transcriber and may not add steps. Check the page
  against "What makes a page work well" in `docs/CONFLUENCE.md`.
* **A T-code you expected is missing** - breakpoint in
  `afsgap/research/tcode.py::TCodeVerifier.verify`; the `dropped` list carries
  the reason. The fetched page is in `.cache/pages/` if you want to read what the
  verifier actually saw.
* **Research looks thin** - run with `-v` and read the `queries_used` in the run
  JSON; if the model ran two searches instead of twelve, raise
  `AFSGAP_SAP_MAX_SEARCHES` or sharpen `sap_area_hints.yaml`.
* **A filter is too aggressive or too lax** - `check-filters` gives instant
  feedback, and `tests/test_tolerance_filter.py` is the place to lock the
  decision in before you change the YAML.
* **Re-running costs money** - stages are cached. Delete a single file under
  `.cache/stages/` to redo just that stage.

## 6. Cost control while developing

Iterate offline, then do one live run per process. Live cost is dominated by the
two research calls; `--skip-industry` halves it while you are still tuning SAP
research, and cached stages make report and analysis changes free.
