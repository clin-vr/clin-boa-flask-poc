package evidence.checks

value_equals(id, ev, key, expected) := f if {
	v := value(ev, key)
	v.found
	ok := v.value == expected
	f := finding(ev, id, pass_fail(ok), expected, v.value, v.location)
} else := finding(ev, id, "NOT_FOUND", expected, null, value(ev, key).location)
