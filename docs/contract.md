# Evidence service contract

This is the request and response contract of `evidence-api`, and the interface a new collector implements. It's written for adding the Jira collector (`collectors/jira.py`) to the same service. The examples at the end are captured from a running stack by `scripts/capture_examples.py`, and `tests/e2e/test_contract_examples.py` checks that they still match live responses.

## Endpoints

| Endpoint | Body | Returns |
|---|---|---|
| `POST /collect` | a control request or a debug request | The envelope without `result` |
| `POST /evaluate` | a control request or a debug request | The envelope with `result` |
| `GET /health` | | `{"status": "ok", "opa": "ok" \| "unreachable", "collectors": [...]}` |

Add `?include_raw=true` to either POST to fill `evidence[].raw` with the full source content.

**Control request:** `{"control_id": "CTL-CDS-001", "params": {"version": "2.6.0"}}`
- `params` is optional. It fills `${placeholders}` in the template and overrides the template's own `params` and the OPA defaults in `opa/data/params.json`.

**Debug request** (SharePoint only): `{"url": "<link>", "policy": "freshness", "selectors": {...}, "params": {...}, "name_pattern": "..."}`
- The URL's host must be in `SHAREPOINT_ALLOWED_HOSTS`.
- A file link becomes an exact reference. A folder link needs `name_pattern`, and the newest match is used.
- Sharing links (`/:x:/r/...`, `Doc.aspx`) are rejected.
- `policy` is required only on `/evaluate`.
- The run has a single layer named `primary`.

### HTTP codes

| Code | When |
|---|---|
| `200` | Any verdict, including `NON_COMPLIANT`, `INDETERMINATE`, and `ERROR` when a source or OPA can't be reached. Read `result.status`, not the HTTP code. |
| `400` | The body isn't a JSON object, has neither `control_id` nor `url`, has invalid `params` or selectors, lacks a placeholder value, or has a debug URL that isn't allowed. |
| `404` | Unknown `control_id`, or a policy OPA doesn't have |

Error bodies are `{"error": "<message>"}`.

## Response envelope

| Field | Meaning |
|---|---|
| `schema_version` | `"1"` |
| `request` | Echo of `control_id`, `url`, `policy`, `selectors` and `params` from the body, whichever were sent |
| `result` | `/evaluate` only. The OPA decision, described below |
| `evidence[]` | One entry per template source, across all layers |
| `meta.evaluated_at` | UTC timestamp |
| `meta.collector` | `evidence-api@<version>` |
| `meta.template_revision` | `sha256:` of the template file, or of the synthesized template for a debug run |
| `meta.policy_revision` | `sha256:` of every Rego module OPA had loaded. `null` on `/collect` or when OPA is down. |
| `meta.executed_as` | The SharePoint principal (`anonymous` against the mock) |
| `meta.duration_ms` | Server-side time for the request |

## Evidence

Every collector returns this shape, and OPA receives the same list as `input.evidence`, with `raw` set to `null`.

| Field | Meaning |
|---|---|
| `layer` | The template layer this source belongs to |
| `source` | The collector name: `sharepoint`, `fixture`, `jira`, and so on |
| `found` | The evidence exists (a file matched, an issue exists) |
| `error` | Set only when the source couldn't be read at all. `found` is then `false`. |
| `subject` | Shared fields, all nullable: `id`, `uri`, `title`, `created_at`, `modified_at` (RFC 3339), `modified_by`, `text`, `owners[]` |
| `subject.owners[]` | `{"value", "source"}` from every place an owner can come from. SharePoint uses `template`, `column:Owner Team`, `sp:Author`, `sp:ModifiedBy`, and `sp:AssociatedOwnerGroup`, one entry per group member. |
| `values` | One entry per template selector: `{"value", "found", "location"}` |
| `raw` | Only with `?include_raw=true` |

The distinction that matters most:
- **Absent evidence** is `found: false` with no `error`. OPA reports that as `NOT_FOUND`, and the layer as `INDETERMINATE`.
- **An unreadable source** has `error` set, and the layer is `ERROR`.
- Missing evidence is never reported as a violation.

## Result

| Field | Meaning |
|---|---|
| `status` | `COMPLIANT` \| `NON_COMPLIANT` \| `INDETERMINATE` \| `ERROR` |
| `color` | `green` only for `COMPLIANT`, otherwise `red` |
| `reason` | One line |
| `layers[]` | `{"name", "kind": "detective" \| "preventive", "status", "reason"}` for every template layer |
| `findings[]` | `{"layer", "check_id", "result": "PASS" \| "FAIL" \| "NOT_FOUND", "expected", "observed", "location"}` |

**Rollup rules** (`opa/checks/rollup.rego`):

1. A layer is `ERROR` if any of its evidence has `error`; else `NON_COMPLIANT` if any finding is `FAIL`; else `INDETERMINATE` if any finding is `NOT_FOUND` or it has no findings; otherwise `COMPLIANT`.
2. The control takes its worst layer, ranked `ERROR` > `NON_COMPLIANT` > `INDETERMINATE` > `COMPLIANT`.
3. A check that compares against another layer, such as `same_value`, returns `NOT_FOUND` when that layer has nothing to compare against. A problem in one layer never makes another layer `FAIL`.

## OPA input

```json
{
  "control": {"control_id": "CTL-CDS-001", "policy": "cds_code_approval",
              "layers": [{"name": "cataloged", "kind": "detective"}, {"name": "enforced", "kind": "preventive"}]},
  "params": {"app_id": "AIT-12345", "version": "2.6.0"},
  "evidence": ["...Evidence entries, raw = null..."]
}
```

The API queries `POST /v1/data/evidence/policies/<policy>/decision`. Checks are functions in package `evidence.checks`, one file each. A policy calls `checks.layer_evidence("<layer>")` and the checks it needs, then returns `checks.decision(findings)`.

## Adding a collector

1. Subclass `evidence_api.collectors.base.Collector`, set `name`, and implement `collect(layer, ref, selectors, *, owner=None, include_raw=False) -> Evidence`.
2. **Never raise** for absent evidence or an unreachable source:
   - Absent: return `Evidence(found=False, values={name: Value(None, False, "<why>") ...})`.
   - Unreachable: return `self.unreachable(layer, exc)`.
   The route also catches unexpected exceptions and turns them into `error`.
3. Fill the `subject` fields your source has, and leave the rest `None`. Add owners with a `source` tag of your own, such as `jira:Assignee`.
4. Resolve selectors into `values`. Reuse `selectors.apply()` for document-shaped content, or add selector types for your source (for example `jira_field`) in `selectors.py`.
5. Register the collector in `create_app()` (`app.py`). Templates then refer to it as `"collector": "jira"`.
6. Limitation: one source per layer. `layer_evidence()` in Rego expects a single entry for each layer name.

## Open questions for Krishna

- Should `evidence[]` stay an array with one entry per source? The alternative is an object keyed by layer.
- Are the seven `subject` fields right for Jira issues? What goes in `modified_by` and `owners[]`?
- `layers`: do the Jira controls have a detective/preventive split, like CDS approvals?
- Should findings carry an `excerpt`, or are `observed` and `location` enough?

## Examples

### `POST /evaluate` for a two-layer control where the enforced layer fails

<!-- example:evaluate-cds -->
```json
{
  "schema_version": "1",
  "request": {
    "control_id": "CTL-CDS-001",
    "params": {
      "version": "2.6.0"
    }
  },
  "result": {
    "color": "red",
    "findings": [
      {
        "check_id": "approval_exists",
        "expected": "document exists",
        "layer": "cataloged",
        "location": "http://sharepoint-mock:8000/sites/cds/Approvals/AIT-12345-2.6.0-approval.docx",
        "observed": "AIT-12345-2.6.0-approval.docx",
        "result": "PASS"
      },
      {
        "check_id": "approval_wording",
        "expected": "contains \"Approved for production deployment\"",
        "layer": "cataloged",
        "location": "section 'Approval Status'",
        "observed": "Approved for production deployment",
        "result": "PASS"
      },
      {
        "check_id": "approver_in_group",
        "expected": "one of sp:AssociatedOwnerGroup",
        "layer": "cataloged",
        "location": "document text, line 5",
        "observed": "B. Approver",
        "result": "PASS"
      },
      {
        "check_id": "signed_on",
        "expected": "signed_on present",
        "layer": "cataloged",
        "location": "document text, line 6",
        "observed": "2026-09-27",
        "result": "PASS"
      },
      {
        "check_id": "gate_policy",
        "expected": "gate_policy present",
        "layer": "enforced",
        "location": "json path gate.policy_ref",
        "observed": "https://git.example.test/cds/policies/blob/3f9c2e1/deploy_gate.rego",
        "result": "PASS"
      },
      {
        "check_id": "gate_allowed",
        "expected": "allow",
        "layer": "enforced",
        "location": "json path gate.result",
        "observed": "allow",
        "result": "PASS"
      },
      {
        "check_id": "gate_checked_approval",
        "expected": "cataloged approval uri (http://sharepoint-mock:8000/sites/cds/Approvals/AIT-12345-2.6.0-approval.docx)",
        "layer": "enforced",
        "location": "json path gate.approval_uri",
        "observed": "http://sharepoint-mock:8000/sites/cds/Approvals/AIT-12345-2.4.0-approval.docx",
        "result": "FAIL"
      }
    ],
    "layers": [
      {
        "kind": "detective",
        "name": "cataloged",
        "reason": "All 4 checks passed",
        "status": "COMPLIANT"
      },
      {
        "kind": "preventive",
        "name": "enforced",
        "reason": "Failed checks: gate_checked_approval",
        "status": "NON_COMPLIANT"
      }
    ],
    "reason": "enforced: Failed checks: gate_checked_approval",
    "status": "NON_COMPLIANT"
  },
  "evidence": [
    {
      "layer": "cataloged",
      "source": "sharepoint",
      "found": true,
      "error": null,
      "subject": {
        "id": "AIT-12345-2.6.0-approval.docx",
        "uri": "http://sharepoint-mock:8000/sites/cds/Approvals/AIT-12345-2.6.0-approval.docx",
        "title": "AIT-12345-2.6.0-approval.docx",
        "created_at": "2026-09-26T17:55:12Z",
        "modified_at": "2026-09-27T17:55:12Z",
        "modified_by": "B. Approver",
        "text": null,
        "owners": [
          {
            "value": "CDS",
            "source": "template"
          },
          {
            "value": "CDS",
            "source": "column:Owner Team"
          },
          {
            "value": "B. Approver",
            "source": "sp:Author"
          },
          {
            "value": "B. Approver",
            "source": "sp:ModifiedBy"
          },
          {
            "value": "A. Approver",
            "source": "sp:AssociatedOwnerGroup"
          },
          {
            "value": "B. Approver",
            "source": "sp:AssociatedOwnerGroup"
          }
        ]
      },
      "values": {
        "status": {
          "value": "Approved for production deployment",
          "found": true,
          "location": "section 'Approval Status'"
        },
        "approver": {
          "value": "B. Approver",
          "found": true,
          "location": "document text, line 5"
        },
        "signed_on": {
          "value": "2026-09-27",
          "found": true,
          "location": "document text, line 6"
        }
      },
      "raw": null
    },
    {
      "layer": "enforced",
      "source": "fixture",
      "found": true,
      "error": null,
      "subject": {
        "id": "deploy-AIT-12345-2.6.0.json",
        "uri": "fixture:ci/deploy-AIT-12345-2.6.0.json",
        "title": "deploy-AIT-12345-2.6.0.json",
        "created_at": null,
        "modified_at": null,
        "modified_by": null,
        "text": null,
        "owners": [
          {
            "value": "CDS",
            "source": "template"
          }
        ]
      },
      "values": {
        "gate_policy": {
          "value": "https://git.example.test/cds/policies/blob/3f9c2e1/deploy_gate.rego",
          "found": true,
          "location": "json path gate.policy_ref"
        },
        "gate_result": {
          "value": "allow",
          "found": true,
          "location": "json path gate.result"
        },
        "approval_uri": {
          "value": "http://sharepoint-mock:8000/sites/cds/Approvals/AIT-12345-2.4.0-approval.docx",
          "found": true,
          "location": "json path gate.approval_uri"
        }
      },
      "raw": null
    }
  ],
  "meta": {
    "evaluated_at": "2026-09-29T17:55:12Z",
    "collector": "evidence-api@0.1.0",
    "template_revision": "sha256:bf34404f93962a569948f45decb4f7ea65b369eafd4d03f27d13fff25bb220f9",
    "policy_revision": "sha256:a7854fd77c88c7fbcfb1b8f4995cccb611fd6fd1d53041b5d1ae4870d2c564b7",
    "executed_as": "anonymous",
    "duration_ms": 28
  }
}
```
<!-- /example -->

### `POST /collect`

<!-- example:collect-cloud -->
```json
{
  "schema_version": "1",
  "request": {
    "control_id": "CTL-CLOUD-001"
  },
  "evidence": [
    {
      "layer": "primary",
      "source": "sharepoint",
      "found": true,
      "error": null,
      "subject": {
        "id": "cloud-approvals-2026-09.xlsx",
        "uri": "http://sharepoint-mock:8000/sites/compliance/Shared%20Documents/APS/cloud-approvals-2026-09.xlsx",
        "title": "cloud-approvals-2026-09.xlsx",
        "created_at": "2026-08-30T17:55:12Z",
        "modified_at": "2026-09-24T17:55:12Z",
        "modified_by": "K. Reviewer",
        "text": null,
        "owners": [
          {
            "value": "Cloud Platform Eng",
            "source": "template"
          },
          {
            "value": "Cloud Platform Eng",
            "source": "column:Owner Team"
          },
          {
            "value": "J. Analyst",
            "source": "sp:Author"
          },
          {
            "value": "K. Reviewer",
            "source": "sp:ModifiedBy"
          },
          {
            "value": "C. Owner",
            "source": "sp:AssociatedOwnerGroup"
          }
        ]
      },
      "values": {
        "approved": {
          "value": "Yes",
          "found": true,
          "location": "sheet 'AITs', row 2, column 'Approved for Cloud'"
        },
        "as_of": {
          "value": "2026-09-24",
          "found": true,
          "location": "sheet 'Info', cell B2"
        },
        "modified": {
          "value": "2026-09-24T17:55:12Z",
          "found": true,
          "location": "sharepoint metadata"
        }
      },
      "raw": null
    }
  ],
  "meta": {
    "evaluated_at": "2026-09-29T17:55:12Z",
    "collector": "evidence-api@0.1.0",
    "template_revision": "sha256:191b49f0af47e35ce689ee217b994002c7b1e631d22a52f69eb672b9072d5927",
    "policy_revision": null,
    "executed_as": "anonymous",
    "duration_ms": 9
  }
}
```
<!-- /example -->

### `404` for an unknown control

<!-- example:unknown-control -->
```json
{
  "error": "No template for control 'CTL-NOPE-001'"
}
```
<!-- /example -->
