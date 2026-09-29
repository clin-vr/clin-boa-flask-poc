package evidence.checks

rank := {"COMPLIANT": 0, "INDETERMINATE": 1, "NON_COMPLIANT": 2, "ERROR": 3}

layer_result(errors, failed, missing, total) := ["ERROR", concat("; ", errors)] if {
	count(errors) > 0
} else := ["NON_COMPLIANT", sprintf("Failed checks: %s", [concat(", ", failed)])] if {
	count(failed) > 0
} else := ["INDETERMINATE", sprintf("Evidence not found: %s", [concat(", ", missing)])] if {
	count(missing) > 0
} else := ["INDETERMINATE", "No checks ran"] if {
	total == 0
} else := ["COMPLIANT", sprintf("All %d checks passed", [total])]

layer_summary(layer, findings) := summary if {
	errors := [e.error | some e in input.evidence; e.layer == layer.name; e.error != null]
	mine := [f | some f in findings; f.layer == layer.name]
	failed := [f.check_id | some f in mine; f.result == "FAIL"]
	missing := [f.check_id | some f in mine; f.result == "NOT_FOUND"]
	[status, reason] := layer_result(errors, failed, missing, count(mine))
	summary := {"name": layer.name, "kind": layer.kind, "status": status, "reason": reason}
}

color("COMPLIANT") := "green"

color(status) := "red" if status != "COMPLIANT"

control_reason(layers, status, findings) := layers[0].reason if {
	count(layers) == 1
} else := sprintf("All %d checks passed across %d layers", [count(findings), count(layers)]) if {
	status == "COMPLIANT"
} else := concat("; ", [sprintf("%s: %s", [l.name, l.reason]) | some l in layers; l.status == status])

decision(findings) := result if {
	layers := [layer_summary(l, findings) | some l in input.control.layers]
	worst := max([rank[l.status] | some l in layers])
	status := [s | some s, r in rank; r == worst][0]
	result := {
		"status": status,
		"color": color(status),
		"reason": control_reason(layers, status, findings),
		"layers": layers,
		"findings": findings,
	}
}
