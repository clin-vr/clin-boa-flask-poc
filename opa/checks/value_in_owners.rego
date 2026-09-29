package evidence.checks

value_in_owners(id, ev, key, source) := f if {
	v := value(ev, key)
	v.found
	members := {o.value | some o in ev.subject.owners; o.source == source}
	ok := count({m | some m in members; m == v.value}) > 0
	f := finding(ev, id, pass_fail(ok), sprintf("one of %s", [source]), v.value, v.location)
} else := finding(ev, id, "NOT_FOUND", sprintf("one of %s", [source]), null, value(ev, key).location)
