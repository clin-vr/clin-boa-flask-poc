package evidence.checks

value_present(id, ev, key) := f if {
	v := value(ev, key)
	v.found
	ok := trim_space(sprintf("%v", [v.value])) != ""
	f := finding(ev, id, pass_fail(ok), sprintf("%s present", [key]), v.value, v.location)
} else := finding(ev, id, "NOT_FOUND", sprintf("%s present", [key]), null, value(ev, key).location)
