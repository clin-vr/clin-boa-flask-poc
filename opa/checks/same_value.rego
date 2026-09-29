package evidence.checks

same_value(id, ev, key, other, description) := f if {
	v := value(ev, key)
	v.found
	other != null
	ok := v.value == other
	f := finding(ev, id, pass_fail(ok), sprintf("%s (%v)", [description, other]), v.value, v.location)
} else := finding(ev, id, "NOT_FOUND", description, null, value(ev, key).location)
