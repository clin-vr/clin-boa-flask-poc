package evidence.policies.policy_exception

import data.evidence.checks

ev := checks.layer_evidence("primary")

findings := [
	checks.exists("exists", ev),
	checks.date_not_expired("not_expired", ev, "expiry"),
	checks.value_present("compensating_controls", ev, "controls"),
]

decision := checks.decision(findings)
