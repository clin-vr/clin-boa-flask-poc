package evidence.policies.cds_code_approval

import data.evidence.checks

p := checks.params("cds_code_approval")

cataloged := checks.layer_evidence("cataloged")

enforced := checks.layer_evidence("enforced")

findings := [
	checks.exists("approval_exists", cataloged),
	checks.text_contains("approval_wording", cataloged, "status", p.required_wording),
	checks.value_in_owners("approver_in_group", cataloged, "approver", p.approver_source),
	checks.value_present("signed_on", cataloged, "signed_on"),
	checks.value_present("gate_policy", enforced, "gate_policy"),
	checks.value_equals("gate_allowed", enforced, "gate_result", p.gate_allow),
	checks.same_value("gate_checked_approval", enforced, "approval_uri", cataloged.subject.uri, "cataloged approval uri"),
]

decision := checks.decision(findings)
