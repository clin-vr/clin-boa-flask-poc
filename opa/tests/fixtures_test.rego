package evidence.tests

# 2026-09-29T12:00:00Z
now := 1790683200000000000

primary := [{"name": "primary", "kind": "detective"}]

cds_layers := [{"name": "cataloged", "kind": "detective"}, {"name": "enforced", "kind": "preventive"}]

control(policy, layers) := {"control_id": "CTL-TEST", "policy": policy, "layers": layers}

hit(v, location) := {"value": v, "found": true, "location": location}

miss := {"value": null, "found": false, "location": "document not found"}

found_ev(layer, subject, values) := {"layer": layer, "source": "sharepoint", "found": true, "error": null, "subject": subject, "values": values, "raw": null}

absent_ev(layer, values) := {"layer": layer, "source": "sharepoint", "found": false, "error": null, "subject": {"owners": [], "uri": null}, "values": values, "raw": null}

error_ev(layer) := {"layer": layer, "source": "sharepoint", "found": false, "error": "SourceUnreachable: SharePoint returned 503", "subject": {"owners": [], "uri": null}, "values": {}, "raw": null}

subject(id, modified, owners) := {"id": id, "uri": sprintf("http://sharepoint-mock:8000/%s", [id]), "title": id, "created_at": "2026-01-01T00:00:00Z", "modified_at": modified, "modified_by": "K. Reviewer", "text": null, "owners": owners}

team := [{"value": "Platform Operations", "source": "column:Owner Team"}]

cds_group := [
	{"value": "A. Approver", "source": "sp:AssociatedOwnerGroup"},
	{"value": "B. Approver", "source": "sp:AssociatedOwnerGroup"},
]

approval_uri(version) := sprintf("http://sharepoint-mock:8000/AIT-12345-%s-approval.docx", [version])

cataloged(version, approver) := found_ev("cataloged", subject(sprintf("AIT-12345-%s-approval.docx", [version]), "2026-09-22T12:00:00Z", cds_group), {
	"status": hit("Approved for production deployment", "section 'Approval Status'"),
	"approver": hit(approver, "document text, line 5"),
	"signed_on": hit("2026-09-22", "document text, line 6"),
})

enforced(gate_uri) := {
	"layer": "enforced", "source": "fixture", "found": true, "error": null, "raw": null,
	"subject": {"id": "deploy.json", "uri": "fixture:ci/deploy.json", "owners": []},
	"values": {
		"gate_policy": hit("https://git.example.test/cds/policies/blob/3f9c2e1/deploy_gate.rego", "json path gate.policy_ref"),
		"gate_result": hit("allow", "json path gate.result"),
		"approval_uri": hit(gate_uri, "json path gate.approval_uri"),
	},
}

cds_input(evidence) := {"control": control("cds_code_approval", cds_layers), "params": {"app_id": "AIT-12345"}, "evidence": evidence}

layer_status(decision, name) := [l.status | some l in decision.layers; l.name == name][0]

finding(decision, id) := [f | some f in decision.findings; f.check_id == id][0]
