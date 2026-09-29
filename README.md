# clin-boa-flask-poc: Evidence Service (Flask + OPA + SharePoint)

**Status: design agreed, not built yet.** This README is the design. Every section below describes what the POC will be.

A containerized Flask API that collects control evidence from SharePoint (and later Jira, Wiz, Splunk and AppHQ), extracts the values a control template asks for, and evaluates them against Rego policies in OPA.

## Why this exists

This is a proof of concept before development moves into the bank environment. It answers two questions:

1. **What can a Python API get out of on-prem SharePoint?** Metadata, owners, document content, and specific cells or sections.
2. **How much of the evaluation can OPA handle?** The goal is to replace custom Python evaluation with Rego.

Saving results is out of scope. Evaluation is the focus.

It rebuilds Bayo's [`bofa-risk-observability-engine-poc`](https://github.com/VerticalRelevance/bofa-risk-observability-engine-poc):

- **Reused:** the SharePoint REST v1 client and the file parsers.
- **Replaced:** its Python evaluator, which becomes OPA.
- **Dropped:** the persistence layer.

## Where it fits (ROE)

```
Control Practice:  GIS (OPA)   CDS (OPA)
                        │
         Triggers:  CI (build) · Schedule
                        │  {control_id}
                        ▼
┌──────────────────────── ROE ─────────────────────────┐
│  Context ───────────────▶ CGA (OPA) ───────▶ Findings │
│  SharePoint · Jira       Rego policies     green/red │
│  (later: Wiz, Splunk,                                │
│   AppHQ)                                             │
│                                                      │
│  this repo:  /collect  = Context                     │
│              /evaluate = Context + CGA               │
└──────────────────────────────────────────────────────┘
```

## Layered evidence

A control is often proven by several **layers** of evidence, each from a different source. Example control: *"This code needs to be approved by CDS."*

| Layer | Kind | Evidence | Source in this POC |
|---|---|---|---|
| `cataloged` | detective | A SharePoint document with a **verified** approval for the app and version | `sharepoint` collector (mock) |
| `enforced` | preventive | The pipeline's OPA gate stops a deployment that has no verified approval. Evidence: a reference to the gate policy's code, plus the log of a successful run that checked the cataloged approval. | `fixture` collector (mocked CI run log) |

Each layer gets its own status, and the control is `COMPLIANT` only if every layer is.

The OPA gate in the pipeline is **evidence that this service collects**; it's owned by a control practice such as CDS. This service's own OPA (CGA) judges whether all the layers hold. That reading of the ROE diagram hasn't been confirmed; see Open items.

## Architecture

```
                         docker compose
┌───────────────────────────────────────────────────────────────────┐
│                                                                   │
│  evidence-api (Flask)                                             │
│  ┌────────────────────────────────────────────┐                   │
│  │ POST /collect, POST /evaluate              │                   │
│  │   1. load control template                 │                   │
│  │   2. run each source's collector ──────────┼──▶ sharepoint-mock│
│  │   3. apply selectors → values              │    (REST v1)      │
│  │   4. build Evidence[]                      │                   │
│  │   5. (/evaluate only) POST input to OPA ───┼──▶ opa :8181      │
│  │   6. wrap in the response envelope         │    checks/        │
│  └────────────────────────────────────────────┘    policies/      │
│                                                    data/          │
└───────────────────────────────────────────────────────────────────┘
```

| Service | What it does |
|---|---|
| `evidence-api` | Flask app. Loads templates, runs collectors, applies selectors, calls OPA, builds the response. |
| `opa` | An OPA server running as a sidecar. Loads `opa/` from a mounted folder with `--watch`, so rule edits don't need a rebuild. |
| `sharepoint-mock` | A small Flask app that returns real SharePoint Server REST v1 responses and file bytes. There is no Docker image of on-prem SharePoint, so this stands in for it. Pointing the API at a real farm is a URL change. |

This is one service with several collectors:

- `sharepoint`: the SharePoint collector, built now.
- `fixture`: reads JSON files from `fixtures/` and stands in for sources that aren't integrated yet, such as CI run logs and code references.
- `jira`: Krishna's.
- Wiz, Splunk and AppHQ are future collectors.

## API

Both endpoints return **200 for every verdict**, including `NON_COMPLIANT`, `INDETERMINATE` and `ERROR`, where `ERROR` means a source couldn't be reached. Callers read `result.status`. The API returns 400 for a malformed request and 404 for an unknown control or policy.

| Endpoint | Body | Returns |
|---|---|---|
| `POST /collect` | `{"control_id": "CTL-CLOUD-001"}` | The envelope, without `result` |
| `POST /evaluate` | `{"control_id": "CTL-CLOUD-001"}` | The envelope, with `result` |
| `POST /collect` or `/evaluate` (debug) | `{"url": "<sharepoint link>", "policy": "freshness", "selectors": {...}, "params": {...}}` | An ad-hoc run with no template and a single `primary` layer. `policy` is required only on `/evaluate`. The URL's host must be in `SHAREPOINT_ALLOWED_HOSTS` (default: the configured SharePoint host). Sharing links (`/:x:/r/…`, `Doc.aspx`) are rejected with 400. |
| `GET /health` | | `{status, opa, sources}` |

Add `?include_raw=true` to include the full parsed document (text, rows, sections) in `evidence[].raw`.

`params` in the request fill `${placeholders}` in the template, for example `{"app_id": "AIT-12345", "version": "2.4.0"}`. That way a pipeline gate can ask at deploy time whether this exact version is approved. Keep `/evaluate` synchronous and quick for that use; whether a gate will actually call it is still to be confirmed.

### Response envelope

```jsonc
{
  "schema_version": "1",
  "request": { "control_id": "CTL-CLOUD-001", "params": {} },
  "result": {                                  // /evaluate only; produced by OPA
    "status": "COMPLIANT",                     // COMPLIANT | NON_COMPLIANT | INDETERMINATE | ERROR
    "color": "green",                          // green only for COMPLIANT, otherwise red
    "reason": "All 3 checks passed",
    "layers": [                                // the control takes its worst layer's status
      { "name": "primary", "kind": "detective", "status": "COMPLIANT", "reason": "All 3 checks passed" }
    ],
    "findings": [
      { "layer": "primary", "check_id": "approved_for_cloud", "result": "PASS",   // PASS | FAIL | NOT_FOUND
        "expected": "Yes", "observed": "Yes",
        "location": "sheet 'AITs', row 14, column 'Approved for Cloud'",
        "excerpt": "AIT-12345 | Payments Gateway | Yes" }
    ]
  },
  "evidence": [                                // one entry per source, across all layers
    {
      "layer": "primary",
      "source": "sharepoint",
      "subject": {                             // shared across sources; every field nullable
        "id": "cloud-approvals-2026-09.xlsx",
        "uri": "http://sharepoint-mock/sites/compliance/Shared Documents/APS/cloud-approvals-2026-09.xlsx",
        "title": "cloud-approvals-2026-09.xlsx",
        "created_at": "2026-01-10T09:00:00Z",
        "modified_at": "2026-09-22T14:52:17Z",
        "modified_by": "K. Reviewer",
        "text": null,                          // included only when a selector or check needs it
        "owners": [
          { "value": "Cloud Platform Eng", "source": "template" },
          { "value": "Cloud Platform Eng", "source": "column:Owner Team" },
          { "value": "J. Analyst",         "source": "sp:Author" },
          { "value": "K. Reviewer",        "source": "sp:ModifiedBy" },
          { "value": "Compliance Owners",  "source": "sp:AssociatedOwnerGroup" }
        ]
      },
      "values": {                              // one entry per template selector
        "approved": { "value": "Yes", "found": true,
                      "location": "sheet 'AITs', row 14, column 'Approved for Cloud'" },
        "as_of":    { "value": "2026-09-01", "found": true, "location": "sheet 'AITs', cell B2" }
      },
      "raw": null                              // filled only with ?include_raw=true
    }
  ],
  "meta": {
    "evaluated_at": "2026-09-29T15:04:05Z",
    "collector": "evidence-api@0.1.0",
    "template_revision": "sha256:…",           // hash of the template that was applied
    "policy_revision": "sha256:…",             // hash of the Rego that was loaded
    "executed_as": "anonymous",
    "duration_ms": 184
  }
}
```

`evidence[]` is the same structure the API sends to OPA as `input.evidence`, so the response shows exactly what the rules judged.

## Control templates

A template is a JSON file, `templates/<control_id>.json`. It lists the evidence layers, where each layer's evidence lives, what to extract, and which policy judges it. A single-layer control has one layer named `primary`.

```jsonc
{
  "control_id": "CTL-CDS-001",
  "description": "Code is approved by CDS before deployment",
  "policy": "cds_code_approval",
  "owner": "CDS",
  "params": { "app_id": "AIT-12345", "version": "2.4.0" },   // defaults; request params override
  "layers": [
    {
      "name": "cataloged", "kind": "detective",
      "sources": [{
        "collector": "sharepoint",
        "ref": {
          "site_url": "http://sharepoint-mock/sites/cds",
          "folder": "/sites/cds/Approvals",
          "name_pattern": "${app_id}-${version}-approval\\.docx",
          "select": "newest"
        },
        "selectors": {
          "status":    { "docx_section": { "heading": "Approval Status" } },
          "approver":  { "text_regex": { "pattern": "Approved by: (.+)" } },
          "signed_on": { "text_regex": { "pattern": "Sign-off date: (\\d{4}-\\d{2}-\\d{2})" } }
        }
      }]
    },
    {
      "name": "enforced", "kind": "preventive",
      "sources": [{
        "collector": "fixture",
        "ref": { "path": "fixtures/ci/deploy-${app_id}-${version}.json" },
        "selectors": {
          "gate_policy":  { "json_path": "gate.policy_ref" },
          "gate_result":  { "json_path": "gate.result" },
          "approval_uri": { "json_path": "gate.approval_uri" }
        }
      }]
    }
  ]
}
```

A `ref` points at an exact file (`"path"`) or at a folder plus a `name_pattern`, with `"select": "newest"` or `"first"`.

### Selectors

| Selector | Example | Returns |
|---|---|---|
| `metadata` | `"modified_at"`, `"created_at"`, `"version_label"`, `"size_bytes"` | A SharePoint file property |
| `column` | `"Owner Team"` | A custom library column, read from `ListItemAllFields` |
| `xlsx_cell` | `{"sheet": "AITs", "cell": "A4"}` | One cell |
| `xlsx_row` | `{"sheet": "AITs", "match": {"Application ID": "AIT-12345"}, "column": "Approved for Cloud"}` | A cell in the first matching row |
| `docx_section` | `{"heading": "Approval"}` | The text under a heading (case-insensitive, prefix match), up to the next heading of the same level |
| `text_regex` | `{"pattern": "Approved by: (.+)"}` | The first capture group, from the full document text (DOCX, PDF or CSV) |
| `json_path` | `"gate.result"` | A value from a JSON source, such as a fixture |

A selector that finds nothing returns `{"found": false}`. OPA turns that into `NOT_FOUND`, never `FAIL`.

## OPA layout

```
opa/
├── checks/                     # one reusable check per file
│   ├── exists.rego             #   the document was found
│   ├── fresh.rego              #   subject.modified_at within params.within_days
│   ├── value_equals.rego       #   values[k].value == expected
│   ├── value_present.rego      #   values[k].found and not empty
│   ├── text_contains.rego      #   a value or section contains the required wording
│   ├── date_not_expired.rego   #   a date value is later than now
│   ├── owners_present.rego     #   subject.owners is not empty
│   ├── value_in_owners.rego    #   a value (the approver) is in an owner source (the CDS group)
│   ├── same_value.rego         #   two values match, including across layers
│   └── rollup.rego             #   findings → layer statuses → control status, color, reason
├── policies/                   # one per named policy; each imports the checks it uses
│   ├── freshness.rego
│   ├── cloud_approval.rego
│   ├── cds_code_approval.rego
│   └── policy_exception.rego
├── data/params.json            # default parameters (windows, required wording)
└── tests/*_test.rego           # opa test fixtures, no SharePoint needed
```

- The API calls `POST /v1/data/evidence/policies/<policy>/decision` with `{"input": {"control": …, "params": …, "evidence": [...]}}`.
- **Parameters:** defaults come from `data.evidence.params.<policy>`, and `params` in the request or template override them.
- **Deciding a layer's status** (`rollup.rego`, the same order as Bayo's collector):
  1. A source in the layer failed to collect: `ERROR`.
  2. Any finding is `FAIL`: `NON_COMPLIANT`.
  3. Any finding is `NOT_FOUND`: `INDETERMINATE`. Missing evidence is never reported as a violation.
  4. Otherwise: `COMPLIANT`.
- **The control's status** is its worst layer: `ERROR`, then `NON_COMPLIANT`, then `INDETERMINATE`, then `COMPLIANT`.
- `color` is `green` only for `COMPLIANT`.

## Scenarios (made-up policies)

Each scenario has a template and generated sample files in the mock, in a passing version and at least one failing version.

| Policy | Evidence | Checks |
|---|---|---|
| `freshness` | Any file; metadata only | `exists`, `fresh` (42 days), `owners_present` |
| `cloud_approval` | An XLSX register of AITs | `exists`, `approved_for_cloud` = "Yes" for the AIT, `fresh` |
| `cds_code_approval` (two layers) | **cataloged:** a DOCX approval for `${app_id}-${version}`<br>**enforced:** a fixture CI run log from the pipeline's OPA gate | **cataloged:** exists; the "Approval Status" section contains the required wording (`params.required_wording`, default "Approved for production deployment"); approver is in the CDS owners group; sign-off date present. Together these make the approval **verified**.<br>**enforced:** gate policy reference present; gate result is `allow`; the gate's `approval_uri` matches the cataloged document's `uri` |
| `policy_exception` | A DOCX exception record | `date_not_expired` on the expiry date, "Compensating Controls" section present |

### Sample matrix

Each control has one template. Its `params` pick which seeded sample is evaluated, and the end-to-end tests assert these results.

| Control | Policy | Request `params` | Sample | Expected |
|---|---|---|---|---|
| `CTL-FRESH-001` | `freshness` | `{"doc": "runbook.docx"}` | modified 3 days ago, has an owner | `COMPLIANT` |
| | | `{"doc": "stale-runbook.docx"}` | modified 90 days ago | `NON_COMPLIANT` |
| | | `{"doc": "missing.docx"}` | not in the library | `INDETERMINATE` |
| `CTL-CLOUD-001` | `cloud_approval` | `{"ait_id": "AIT-12345"}` | row says "Yes" | `COMPLIANT` |
| | | `{"ait_id": "AIT-67890"}` | row says "No" | `NON_COMPLIANT` |
| | | `{"ait_id": "AIT-99999"}` | no matching row | `INDETERMINATE` |
| `CTL-CDS-001` | `cds_code_approval` | `{"version": "2.4.0"}` | verified approval; the gate allowed it and references the approval | `COMPLIANT` |
| | | `{"version": "2.5.0"}` | approver isn't in the CDS group | `NON_COMPLIANT` (`cataloged`) |
| | | `{"version": "2.6.0"}` | the gate log's `approval_uri` points to another document | `NON_COMPLIANT` (`enforced`) |
| | | `{"version": "3.0.0"}` | no approval document and no gate log | `INDETERMINATE` |
| `CTL-EXC-001` | `policy_exception` | `{"exception_id": "EXC-001"}` | expires in 60 days, has compensating controls | `COMPLIANT` |
| | | `{"exception_id": "EXC-002"}` | expired 10 days ago | `NON_COMPLIANT` |
| | | `{"exception_id": "EXC-003"}` | no "Compensating Controls" section | `INDETERMINATE` |
| `CTL-FRESH-001` | `freshness` | `{"doc": "runbook.docx"}` with the mock container stopped | the source can't be reached | `ERROR` |

The dates in `seed/manifest.json` are offsets from today (for example `"-3d"`), so the expected results don't drift over time. The CI gate fixtures use the same `app_id` and `version` names as the approval documents.

## SharePoint mock

The mock implements these SharePoint Server REST v1 calls, with the same paths and JSON shapes as a real farm:

| Call | Used for |
|---|---|
| `GET /_api/web/GetFileByServerRelativeUrl('…')` with `$expand=Author,ModifiedBy` | File properties, author and modifier |
| `GET …/ListItemAllFields` | Custom columns such as `Owner Team` |
| `GET …/$value` | File bytes |
| `GET /_api/web/GetFolderByServerRelativeUrl('…')/Files` | Folder listing, for folder + pattern refs |
| `GET /_api/web/AssociatedOwnerGroup/Users` | The site owners group |

Sample files are generated by `sharepoint-mock/generate_samples.py`, and their metadata is set in `sharepoint-mock/seed/manifest.json`.

## Planned repo layout

```
clin-boa-flask-poc/
├── docker-compose.yml
├── api/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── evidence_api/
│       ├── app.py             # routes
│       ├── envelope.py        # response builder and revision hashes
│       ├── evidence.py        # Evidence, Subject and Owner dataclasses
│       ├── templates.py       # template loader
│       ├── selectors.py       # selector engine
│       ├── opa_client.py
│       ├── parsing.py         # ported from Bayo's repo
│       └── collectors/
│           ├── base.py        # Collector interface
│           ├── sharepoint.py  # ported REST v1 client, with the ModifiedBy expand fix
│           ├── fixture.py     # reads fixtures/*.json for sources not integrated yet
│           └── jira.py        # Krishna
├── templates/*.json
├── fixtures/ci/*.json         # mocked pipeline OPA gate run logs
├── opa/                       # see OPA layout above
├── sharepoint-mock/
│   ├── Dockerfile
│   ├── app.py
│   ├── generate_samples.py
│   └── seed/
├── tests/
│   ├── unit/
│   └── e2e/                   # runs against the compose stack
└── docs/contract.md           # the envelope and Evidence contract, shared with Jira
```

## Defaults

| Area | Default |
|---|---|
| Stack | Python 3.11, Flask 3, gunicorn, OPA 1.x image (pinned) |
| Auth | None on the API. The mock accepts anonymous calls. `AuthStrategy` (NTLM, Kerberos) stays pluggable but isn't exercised. |
| Runs | One control per call. A scheduler loops over controls; there is no batch endpoint. |
| Storage | None. Nothing is written to disk. |

## Deferred

- **Format coverage** (a separate set of requirements, to come back to): samples and tests for PDF, CSV and XML/InfoPath. Every format is meant to be covered eventually. The POC starts with DOCX and XLSX; the other parsers are ported from Bayo's repo but untested.
- SharePoint lists (`/lists/GetByTitle(...)/items`)
- Site pages (`WikiField`, modern `CanvasContent1`)
- A real SharePoint farm (a trial VM) or SharePoint Online through Graph
- Persistence (Oracle or JSONL)
- Wiz, Splunk and AppHQ collectors
- Real CI-log and code-repo collectors (the `fixture` collector stands in for them)
- A batch or scheduled run endpoint

## Open items

- **Why step 5 ("check against OPA") is struck through in the ROE diagram.** Best guess: the practices' OPA (CDS, GIS) is the preventive pipeline gate, which is evidence rather than our evaluator. Having both `/collect` and `/evaluate` works whichever way it's meant.
- **Whether the pipeline gate will call `/evaluate` at deploy time**, or look up the approval some other way.
- **What makes an approval "verified" at the bank.** The POC assumes: status is Approved, the approver is in the CDS group, and a sign-off date is set.
- **The real CI system and log format**, which decides what a real collector would replace `fixture` with.
- **Contract review with Krishna:** the `evidence[]` array, `layers`, the `subject` fields, and the finding format.
- **GIS and CDS conventions:** whether they already use Rego or input conventions worth copying.

## Build plan

Each build task is followed by a test task. A task starts only when the tests it's blocked by have passed. After P0, P1 and P2 run in parallel, and P3 starts once the Evidence format is fixed (task 8).

| Phase | Produces | Verified by |
|---|---|---|
| **P0 Foundation** | The compose stack: `evidence-api`, `opa`, `sharepoint-mock` | All three services pass their health checks |
| **P1 Evidence data** | Generated samples for every matrix row, and the REST v1 mock | Samples match the matrix; the mock's responses match real SharePoint shapes |
| **P2 Collection** | The `Evidence` model, parsing, templates, selectors, collectors | Unit tests, plus component tests against the live mock |
| **P3 Evaluation** | Rego checks, rollup, the 4 policies, parameter defaults | `opa test`: 14 matrix cases plus the rollup order |
| **P4 API** | `/collect`, `/evaluate`, the envelope, the debug path | API unit tests |
| **P5 Acceptance** | End-to-end suite from the matrix | All 14 rows on a fresh stack, a hot-reload test, a latency report |
| **P6 Handoff** | `docs/contract.md`, `docs/findings.md` | The contract examples match live responses |
| **P7 Format coverage** (deferred) | PDF, CSV and XML samples and selectors | Needs its own requirements round first |

| # | Phase | Task | Blocked by |
|---|---|---|---|
| 1 | P0 | Scaffold the repo and compose stack: mounts, env vars (`SHAREPOINT_BASE_URL`, `SHAREPOINT_ALLOWED_HOSTS`, `OPA_URL`), make targets | none |
| 2 | P0 | **Test:** `compose up --wait`, all 3 health checks return 200, the unit smoke test passes | 1 |
| 3 | P1 | Sample generator and seed manifest: relative dates, `Owner_x0020_Team`, owner group, CI fixtures, URIs built from the base URL | 2 |
| 4 | P1 | **Test:** samples match the matrix, including the deliberately missing files | 3 |
| 5 | P1 | Mock endpoints: 5 REST v1 calls, verbose and nometadata OData, dates computed per request | 4 |
| 6 | P1 | **Test:** response shapes, `$expand`, internal column names, 404s, `$value` SHA-256 | 5 |
| 7 | P2 | `Evidence` models, the collector interface, port `parsing.py` (heading levels on DOCX sections, a JSON parser) | 2 |
| 8 | P2 | **Test:** every sample parses, and the serialized Evidence matches `tests/golden/evidence.json` | 7, 4 |
| 9 | P2 | Template loader (layers, `${param}`, validation, revision hash), the 4 templates, the selector engine | 8 |
| 10 | P2 | **Test:** templates load; each selector hits and misses correctly | 9 |
| 11 | P2 | SharePoint collector (`ModifiedBy` fix, owners, newest match) and `fixture` collector | 10, 6 |
| 12 | P2 | **Test:** fake-session unit tests, and component tests on every sample against the live mock | 11 |
| 13 | P3 | Rego checks (RFC 3339 and date-only dates), rollup, 4 policies, `params.json` | 8 |
| 14 | P3 | **Test:** `opa fmt`, `check`, `test`: 14 matrix cases, rollup order, parameter overrides, frozen time | 13 |
| 15 | P4 | Routes, debug path (host allowlist, sharing links rejected), OPA client, envelope, revision hashes, gunicorn | 12, 14 |
| 16 | P4 | **Test:** HTTP codes, envelope, `include_raw`, debug path, OPA down and source down both give `ERROR` | 15 |
| 17 | P5 | End-to-end suite from the matrix; the `ERROR` row stops the mock; hot-reload test; latency report | 2, 4, 6, 8, 10, 12, 14, 16 |
| 18 | P5 | **Test:** full end-to-end on a fresh stack, and all other suites still green. The last build gate. | 17 |
| 19 | P6 | `docs/contract.md` for Krishna, with examples captured from the live run | 18 |
| 20 | P6 | **Test:** the contract examples match live responses | 19 |
| 21 | P6 | `docs/findings.md` (including the library list for bank approval); mark the README and the design page as built | 18, 20 |
| 22 | P7 | Deferred: format coverage requirements for PDF, CSV, XML | 18 |
