package evidence.checks

date_not_expired(id, ev, key) := f if {
	v := value(ev, key)
	v.found
	expires := parse_time(v.value)
	ok := expires > time.now_ns()
	f := finding(ev, id, pass_fail(ok), "date in the future", v.value, v.location)
} else := finding(ev, id, "NOT_FOUND", "date in the future", null, value(ev, key).location)
