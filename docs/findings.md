# Findings

What the POC showed about pulling control evidence out of on-prem SharePoint with Python, and how much of the judgment OPA can carry. Everything here was run against the REST v1 mock in `sharepoint-mock/`, not a real farm; the gaps that leaves are listed below.

Test status on a fresh `docker compose up --build`:

| Suite | Count |
|---|---|
| unit | 104 |
| component (live mock) | 13 |
| `opa test` | 22 |
| end-to-end | 37 |

## What SharePoint REST v1 gives a Python client

| Need | Call | What comes back |
|---|---|---|
| File metadata | `GetFileByServerRelativeUrl('<path>')` | `Name`, `Length` (as a string), `TimeCreated`, `TimeLastModified`, `UIVersionLabel` |
| Who created and last changed it | same call with `$expand=Author,ModifiedBy` | `Title`, `Email`, `LoginName`. **Without `$expand` these are missing (nometadata) or `__deferred` links (verbose).** This is why Bayo's client always got an empty `modified_by`. |
| Custom library columns | `…/ListItemAllFields` | Keyed by **internal name**: a column displayed as "Owner Team" is `Owner_x0020_Team`. Templates use the display name, and `selectors.internal_column_name()` encodes it. |
| Content | `…/$value` | Raw bytes. Parsing is entirely in Python. |
| Finding "the latest signoff" | `GetFolderByServerRelativeUrl('<folder>')/Files` | The listing already carries `TimeLastModified`, so picking the newest match costs no extra metadata calls. |
| Site owners | `AssociatedOwnerGroup/Users` | The group's members. The approver check uses members, not the group title. |

Response format depends on the `Accept` header: `odata=verbose` wraps everything in `d` / `d.results`, while `nometadata` returns flat objects and `value` arrays. On-prem 2013 defaults to verbose. The collector asks for nometadata and unwraps both.

**Owner** can come from five places, and they rarely agree. The service returns all of them as `subject.owners[]`, tagged `template`, `column:Owner Team`, `sp:Author`, `sp:ModifiedBy` and `sp:AssociatedOwnerGroup`, and leaves the choice to policy.

### Content extraction (Python, not OPA)

| Format | What worked |
|---|---|
| XLSX | Every sheet read into header-keyed rows plus A1 cells. `xlsx_row` ("the row where Application ID = X, column Y") and `xlsx_cell` ("Info!B2") both work. Row 1 is always the header, so a sheet with a title block above its table needs a separate sheet or a header-row option. |
| DOCX | Sections found by heading **style** with their level, so "Approval Status" returns its text up to the next heading of the same level. A document that fakes headings with bold body text won't be split. |
| Free text | `text_regex` finds values such as `Approved by: (.+)` and `Expiry date: (\d{4}-\d{2}-\d{2})`. |
| JSON | Dotted `json_path` into fixture data. |
| PDF, CSV, XML/InfoPath | Parsers ported from Bayo's repo, but **not tested**. That's task 22 and its own requirements round. |

Each extracted value carries a `location` ("sheet 'AITs', row 2, column 'Approved for Cloud'", "section 'Approval Status'", "document text, line 6"), which becomes the finding's location.

## How much OPA can carry

Once evidence is JSON, **all of the judgment moved to Rego**, including the status rules that were Python in Bayo's version:

| In Rego | Notes |
|---|---|
| Existence, equality, required wording (case-insensitive), non-empty values | `exists`, `value_equals`, `text_contains`, `value_present` |
| Freshness and expiry | `time.now_ns()` with `time.parse_rfc3339_ns` and `time.parse_ns("2006-01-02", …)` for date-only values |
| Membership | The approver must be one of the owner-group members |
| Cross-layer checks | The CI gate's `approval_uri` must equal the cataloged document's `uri` |
| Missing ≠ failed | A missing value is `NOT_FOUND`, which makes the layer `INDETERMINATE`, never `NON_COMPLIANT` |
| Layer and control rollup, color, reason | `rollup.rego`: a layer is `ERROR`, then `NON_COMPLIANT`, then `INDETERMINATE`, then `COMPLIANT`; the control takes the worst layer |
| Parameters | Defaults in `data/params.json`, overridden per request |

What stays in Python:
- Fetching (authentication, OData, folder search).
- Parsing binary formats.
- Turning a selector into a value with a location.

OPA can make HTTP calls (`http.send`), but using it to fetch would move credentials and retries into policy code, which is the wrong direction for a bank.

Rego-specific findings:

- **Rule edits need no redeploy.** `opa run --watch` picked up a change to `params.json` through a read-only bind mount on Docker Desktop for macOS within the end-to-end test's polling window. It flipped `CTL-FRESH-001` from `COMPLIANT` to `NON_COMPLIANT` and back.
- **Data file paths follow directories.** Loading `/policies` as one root puts `data/params.json` at `data.data…`. The server and the tests load `checks`, `policies` and `data` as separate roots.
- **One source per layer.** `layer_evidence()` returns a single entry per layer name, and two entries for one layer is a conflict error. Supporting several sources per layer means writing checks over arrays.
- **Cross-layer checks depend on the other layer.** If the cataloged layer errors or is missing, the gate's URI check has nothing to compare against and returns `NOT_FOUND`. An early test expected a `FAIL` there; the policy was right.
- **Time is testable.** `with time.now_ns as <ns>` freezes the clock, so `opa test` covers all 14 matrix outcomes with no SharePoint running.
- `meta.policy_revision` hashes the module sources from OPA's `/v1/policies`, so a silent rule edit shows up in every response.

## Latency

`/evaluate` over 20 runs per control, on a laptop against the local stack (from `reports/latency.json`):

| Control | p50 ms | p95 ms |
|---|---|---|
| CTL-FRESH-001 (metadata only) | 5.4 | 11.4 |
| CTL-CLOUD-001 (XLSX download) | 8.1 | 10.8 |
| CTL-CDS-001 (DOCX + fixture, two layers) | 11.8 | 14.1 |
| CTL-EXC-001 (DOCX) | 10.0 | 11.1 |

That's fast enough for a pipeline gate to call at deploy time, but real SharePoint latency (NTLM handshakes, larger files, a remote farm) will dominate. Measure again against a real farm before relying on it.

## Libraries needing bank approval

Generated by `python scripts/list_dependencies.py` from what the code actually imports:

| Import | Distribution | Used by |
|---|---|---|
| `flask` | Flask | API and mock |
| `requests`, `urllib3` | requests, urllib3 | SharePoint collector, OPA client |
| `docx` | python-docx | DOCX parsing, sample generation |
| `openpyxl` | openpyxl | XLSX parsing, sample generation |
| `pdfplumber`, `pypdf` | pdfplumber, pypdf | PDF parsing (imported lazily, not installed yet) |
| `requests_ntlm`, `requests_kerberos` | requests-ntlm, requests-kerberos | Auth strategies (imported lazily, not installed yet) |

The script can't see these, because they aren't imported:
- `gunicorn`, the WSGI server in both images.
- The OPA server itself (`openpolicyagent/opa:1.21.0`).
- `pytest`, used only in development.

## Gaps against a real farm

- **Auth:** only anonymous calls were exercised. NTLM and Kerberos are ported but untested. The mechanism the bank uses decides `meta.executed_as`.
- **Farm version and OData defaults:** unconfirmed. Both response formats are handled, but a 2013 farm may differ in other fields.
- **Sharing links** (`/:w:/r/…`, `Doc.aspx?sourcedoc=`) are rejected. Resolving them needs an extra API call that wasn't built.
- **Throttling and large files:** not tested. The collector retries once on 429/502/503/504 with a 5 s timeout.
- **Lists and site pages** are deferred, so an AIT register kept as a native SharePoint list isn't covered yet.
- **The preventive layer is a fixture.** The real CI system, its log format and where the gate policy lives are unknown.

## Open items

- Why step 5 ("check against OPA") is struck through in the ROE diagram. `/collect` and `/evaluate` cover either reading.
- Whether the pipeline gate will call `/evaluate` at deploy time.
- What "verified" means for an approval at the bank. The POC uses: the required wording, an approver in the CDS owners group, and a sign-off date.
- The contract review with Krishna. The questions are listed in `docs/contract.md`.
- Whether GIS and CDS already have Rego or input conventions worth copying.
