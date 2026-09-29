package evidence.checks

fresh(id, ev, days) := f if {
	ev.found
	modified := parse_time(ev.subject.modified_at)
	ok := (time.now_ns() - modified) <= (days * 86400000000000)
	f := finding(ev, id, pass_fail(ok), sprintf("modified within %v days", [days]), ev.subject.modified_at, "sharepoint metadata")
} else := finding(ev, id, "NOT_FOUND", sprintf("modified within %v days", [days]), null, "no modified date")
