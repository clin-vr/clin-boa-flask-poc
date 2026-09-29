package evidence.policies.cloud_approval

import data.evidence.checks

p := checks.params("cloud_approval")

ev := checks.layer_evidence("primary")

findings := [
	checks.exists("exists", ev),
	checks.value_equals("approved_for_cloud", ev, "approved", p.approved_value),
	checks.fresh("fresh", ev, p.within_days),
]

decision := checks.decision(findings)
