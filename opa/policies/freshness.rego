package evidence.policies.freshness

import data.evidence.checks

p := checks.params("freshness")

ev := checks.layer_evidence("primary")

findings := [
	checks.exists("exists", ev),
	checks.fresh("fresh", ev, p.within_days),
	checks.owners_present("owners_present", ev),
]

decision := checks.decision(findings)
