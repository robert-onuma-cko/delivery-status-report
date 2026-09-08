"""
Local test harness for delivery_status_report.py.

Runs the Zapier step exactly the way Zapier would (every input arrives as a
JSON string) using sample_input.json, then writes sample_output.html and
sample_output.md next to this file.

    python test_delivery_status_report.py
"""
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "delivery_status_report.py"

sample = json.loads((HERE / "sample_input.json").read_text(encoding="utf-8"))
print("Sample list lengths:", {key: len(value) for key, value in sample.items()})

# Zapier hands every input to the Code step as a string; the upstream step
# json.dumps() each list, so mimic that here.
input_data = {key: json.dumps(value) for key, value in sample.items()}

namespace = {"input_data": input_data, "__name__": "zapier_code_step"}
exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), namespace)
output = namespace["output"]

(HERE / "sample_output.html").write_text(output["report_html"], encoding="utf-8")
(HERE / "sample_output.md").write_text(output["report_markdown"], encoding="utf-8")

# --- parser robustness checks -------------------------------------------------
parse_list = namespace["parse_list"]
normalise_groups = namespace["normalise_groups"]
assert parse_list("['a', None, 'b']", "x", []) == ["a", None, "b"]       # Python literal
assert parse_list('["a", null]', "x", []) == ["a", None]                  # JSON
assert parse_list("a,b,c", "x", []) == ["a", "b", "c"]                     # Zapier comma-joined line items
assert parse_list("", "x", []) == [] and parse_list(None, "x", []) == []
assert normalise_groups("['Rules Engine', 'Ledger']") == ["Rules Engine", "Ledger"]
assert normalise_groups(None) == ["Unassigned"] and normalise_groups("Nexus") == ["Nexus"]

# --- empty input must not crash -----------------------------------------------
build_report = namespace["build_report"]
empty = build_report({})
assert empty["item_count"] == 0 and "No work items" in empty["warnings"]

# --- optional urls turn item names into links ---------------------------------
linked = build_report({
    "summary": ["[Delivery] Ledger", "Plain item"],
    "strategic_initiatives": ["SSOT", "SSOT"],
    "product_group": [["Ledger"], ["Ledger"]],
    "delivery_comment": ["ok", None],
    "delivery_rag": ["On track", "Done"],
    "urls": ["https://checkout.atlassian.net/browse/FIN-1", ""],
})
assert 'href="https://checkout.atlassian.net/browse/FIN-1"' in linked["report_html"]
assert "[\\[Delivery\\] Ledger](https://checkout.atlassian.net/browse/FIN-1)" in linked["report_markdown"]
assert "**Plain item**" in linked["report_markdown"]

# --- importing the module must not execute the Zapier entry point -------------
sys.path.insert(0, str(HERE))
import delivery_status_report  # noqa: E402
assert not hasattr(delivery_status_report, "output")

print()
print("Output keys:", list(output))
print("Title:", output["title"])
print("RAG summary:", output["rag_summary"])
print("Counts:", output["item_count"], "items /", output["initiative_count"], "initiatives /",
      output["product_group_count"], "product groups")
print("Warnings:", output["warnings"] or "(none)")
print("HTML size:", len(output["report_html"]), "chars; Markdown size:", len(output["report_markdown"]), "chars")
print("All checks passed.")
