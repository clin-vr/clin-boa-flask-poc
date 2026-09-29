package evidence.tests

import data.evidence.policies

# CTL-FRESH-001

fresh_input(ev) := {"control": control("freshness", primary), "params": {}, "evidence": [ev]}

test_fresh_runbook_compliant if {
	d := policies.freshness.decision with input as fresh_input(found_ev("primary", subject("runbook.docx", "2026-09-26T12:00:00Z", team), {})) with time.now_ns as now
	d.status == "COMPLIANT"
	d.color == "green"
}

test_stale_runbook_non_compliant if {
	d := policies.freshness.decision with input as fresh_input(found_ev("primary", subject("stale-runbook.docx", "2026-07-01T12:00:00Z", team), {})) with time.now_ns as now
	d.status == "NON_COMPLIANT"
	d.color == "red"
	finding(d, "fresh").result == "FAIL"
}

test_missing_runbook_indeterminate if {
	d := policies.freshness.decision with input as fresh_input(absent_ev("primary", {})) with time.now_ns as now
	d.status == "INDETERMINATE"
	d.color == "red"
	every f in d.findings { f.result == "NOT_FOUND" }
}

test_unreachable_source_error if {
	d := policies.freshness.decision with input as fresh_input(error_ev("primary")) with time.now_ns as now
	d.status == "ERROR"
	d.color == "red"
	contains(d.reason, "SharePoint returned 503")
}

test_params_override_window if {
	stale := fresh_input(found_ev("primary", subject("stale-runbook.docx", "2026-07-01T12:00:00Z", team), {}))
	d := policies.freshness.decision with input as object.union(stale, {"params": {"within_days": 120}}) with time.now_ns as now
	d.status == "COMPLIANT"
}

# CTL-CLOUD-001

cloud_input(approved) := {"control": control("cloud_approval", primary), "params": {}, "evidence": [found_ev("primary", subject("cloud-approvals-2026-09.xlsx", "2026-09-24T12:00:00Z", team), {"approved": approved})]}

test_cloud_yes_compliant if {
	d := policies.cloud_approval.decision with input as cloud_input(hit("Yes", "sheet 'AITs', row 2, column 'Approved for Cloud'")) with time.now_ns as now
	d.status == "COMPLIANT"
}

test_cloud_no_non_compliant if {
	d := policies.cloud_approval.decision with input as cloud_input(hit("No", "sheet 'AITs', row 3, column 'Approved for Cloud'")) with time.now_ns as now
	d.status == "NON_COMPLIANT"
	finding(d, "approved_for_cloud").observed == "No"
}

test_cloud_missing_row_indeterminate_not_fail if {
	d := policies.cloud_approval.decision with input as cloud_input({"value": null, "found": false, "location": "sheet 'AITs', no row matching"}) with time.now_ns as now
	d.status == "INDETERMINATE"
	finding(d, "approved_for_cloud").result == "NOT_FOUND"
}

# CTL-CDS-001

test_cds_verified_compliant if {
	d := policies.cds_code_approval.decision with input as cds_input([cataloged("2.4.0", "A. Approver"), enforced(approval_uri("2.4.0"))]) with time.now_ns as now
	d.status == "COMPLIANT"
	d.color == "green"
	d.reason == "All 7 checks passed across 2 layers"
	layer_status(d, "cataloged") == "COMPLIANT"
	layer_status(d, "enforced") == "COMPLIANT"
}

test_cds_outsider_approver_fails_cataloged if {
	d := policies.cds_code_approval.decision with input as cds_input([cataloged("2.5.0", "Z. Outsider"), enforced(approval_uri("2.5.0"))]) with time.now_ns as now
	d.status == "NON_COMPLIANT"
	layer_status(d, "cataloged") == "NON_COMPLIANT"
	layer_status(d, "enforced") == "COMPLIANT"
	finding(d, "approver_in_group").result == "FAIL"
	startswith(d.reason, "cataloged:")
}

test_cds_gate_mismatch_fails_enforced if {
	d := policies.cds_code_approval.decision with input as cds_input([cataloged("2.6.0", "B. Approver"), enforced(approval_uri("2.4.0"))]) with time.now_ns as now
	d.status == "NON_COMPLIANT"
	layer_status(d, "cataloged") == "COMPLIANT"
	layer_status(d, "enforced") == "NON_COMPLIANT"
	finding(d, "gate_checked_approval").result == "FAIL"
}

test_cds_nothing_found_indeterminate if {
	fixture_missing := object.union(absent_ev("enforced", {}), {"source": "fixture"})
	d := policies.cds_code_approval.decision with input as cds_input([absent_ev("cataloged", {}), fixture_missing]) with time.now_ns as now
	d.status == "INDETERMINATE"
	layer_status(d, "cataloged") == "INDETERMINATE"
	layer_status(d, "enforced") == "INDETERMINATE"
}

test_cds_missing_wording_fails if {
	ev := cataloged("2.4.0", "A. Approver")
	reworded := object.union(ev, {"values": {"status": hit("Pending review", "section 'Approval Status'")}})
	d := policies.cds_code_approval.decision with input as cds_input([reworded, enforced(approval_uri("2.4.0"))]) with time.now_ns as now
	finding(d, "approval_wording").result == "FAIL"
	d.status == "NON_COMPLIANT"
}

# CTL-EXC-001

exc_input(expiry, controls) := {"control": control("policy_exception", primary), "params": {}, "evidence": [found_ev("primary", subject("EXC.docx", "2026-09-09T12:00:00Z", team), {"expiry": expiry, "controls": controls})]}

controls := hit("Weekly manual review of privileged access by the Risk Office.", "section 'Compensating Controls'")

test_exception_unexpired_compliant if {
	d := policies.policy_exception.decision with input as exc_input(hit("2026-11-28", "document text, line 3"), controls) with time.now_ns as now
	d.status == "COMPLIANT"
}

test_exception_expired_non_compliant if {
	d := policies.policy_exception.decision with input as exc_input(hit("2026-09-19", "document text, line 3"), controls) with time.now_ns as now
	d.status == "NON_COMPLIANT"
	finding(d, "not_expired").result == "FAIL"
}

test_exception_without_controls_indeterminate if {
	d := policies.policy_exception.decision with input as exc_input(hit("2026-11-28", "document text, line 3"), {"value": null, "found": false, "location": "no section headed 'Compensating Controls'"}) with time.now_ns as now
	d.status == "INDETERMINATE"
	finding(d, "compensating_controls").result == "NOT_FOUND"
}

test_exception_accepts_rfc3339_expiry if {
	d := policies.policy_exception.decision with input as exc_input(hit("2026-11-28T00:00:00Z", "x"), controls) with time.now_ns as now
	d.status == "COMPLIANT"
}
