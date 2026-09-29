package evidence.checks

exists(id, ev) := finding(ev, id, "PASS", "document exists", ev.subject.id, ev.subject.uri) if {
	ev.found
} else := finding(ev, id, "NOT_FOUND", "document exists", null, "no matching document")
