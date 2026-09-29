package evidence.checks

owners_present(id, ev) := f if {
	ev.found
	owners := [o.value | some o in ev.subject.owners]
	f := finding(ev, id, pass_fail(count(owners) > 0), "at least one owner", owners, "owners")
} else := finding(ev, id, "NOT_FOUND", "at least one owner", null, "no matching document")
