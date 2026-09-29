package evidence.checks

text_contains(id, ev, key, needle) := f if {
	v := value(ev, key)
	v.found
	ok := indexof(lower(sprintf("%v", [v.value])), lower(needle)) >= 0
	f := finding(ev, id, pass_fail(ok), sprintf("contains %q", [needle]), v.value, v.location)
} else := finding(ev, id, "NOT_FOUND", sprintf("contains %q", [needle]), null, value(ev, key).location)
