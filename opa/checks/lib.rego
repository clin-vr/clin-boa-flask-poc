package evidence.checks

params(policy) := object.union(object.get(data.evidence.params, policy, {}), object.get(input, "params", {}))

missing_evidence(name) := {"layer": name, "found": false, "error": null, "values": {}, "subject": {"owners": [], "uri": null}}

layer_evidence(name) := e if {
	some e in input.evidence
	e.layer == name
} else := missing_evidence(name)

value(ev, key) := object.get(ev.values, key, {"value": null, "found": false, "location": sprintf("no selector %q", [key])})

finding(ev, id, result, expected, observed, location) := {
	"layer": ev.layer,
	"check_id": id,
	"result": result,
	"expected": expected,
	"observed": observed,
	"location": location,
}

pass_fail(true) := "PASS"

pass_fail(false) := "FAIL"

parse_time(s) := time.parse_rfc3339_ns(s) if {
	regex.match(`^\d{4}-\d{2}-\d{2}T`, s)
} else := time.parse_ns("2006-01-02", s) if {
	regex.match(`^\d{4}-\d{2}-\d{2}$`, s)
}
