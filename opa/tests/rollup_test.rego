package evidence.tests

import data.evidence.policies

test_error_outranks_non_compliant if {
	denied := object.union(enforced(approval_uri("2.4.0")), {"values": {"gate_result": hit("deny", "json path gate.result")}})
	d := policies.cds_code_approval.decision with input as cds_input([error_ev("cataloged"), denied]) with time.now_ns as now
	d.status == "ERROR"
	layer_status(d, "cataloged") == "ERROR"
	layer_status(d, "enforced") == "NON_COMPLIANT"
}

test_non_compliant_outranks_indeterminate if {
	fixture_missing := object.union(absent_ev("enforced", {}), {"source": "fixture"})
	d := policies.cds_code_approval.decision with input as cds_input([cataloged("2.5.0", "Z. Outsider"), fixture_missing]) with time.now_ns as now
	d.status == "NON_COMPLIANT"
	layer_status(d, "enforced") == "INDETERMINATE"
}

test_indeterminate_outranks_compliant if {
	fixture_missing := object.union(absent_ev("enforced", {}), {"source": "fixture"})
	d := policies.cds_code_approval.decision with input as cds_input([cataloged("2.4.0", "A. Approver"), fixture_missing]) with time.now_ns as now
	d.status == "INDETERMINATE"
	layer_status(d, "cataloged") == "COMPLIANT"
}

test_layer_without_evidence_still_reports if {
	d := policies.cds_code_approval.decision with input as cds_input([cataloged("2.4.0", "A. Approver")]) with time.now_ns as now
	layer_status(d, "enforced") == "INDETERMINATE"
	finding(d, "gate_allowed").layer == "enforced"
}

test_every_finding_is_tagged_with_a_layer if {
	d := policies.cds_code_approval.decision with input as cds_input([cataloged("2.4.0", "A. Approver"), enforced(approval_uri("2.4.0"))]) with time.now_ns as now
	count(d.findings) == 7
	every f in d.findings { f.layer in {"cataloged", "enforced"} }
}
