"""
Local test harness for delivery_status_report.py.

Runs the Zapier step exactly the way Zapier would (every input arrives as a
JSON string) using sample_input.json, then writes sample_output.html and
sample_output.md next to this file. After that it checks the parsing, linking,
sorting and change-tracking rules with small hand-made inputs.

    python test_delivery_status_report.py
"""
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "delivery_status_report.py"
UTC = timezone.utc
BROWSE = "https://checkout.atlassian.net/browse/"

sample = json.loads((HERE / "sample_input.json").read_text(encoding="utf-8"))
print("Sample list lengths:", {key: len(value) for key, value in sample.items() if isinstance(value, list)})

# Zapier hands every input to the Code step as a string; the upstream step
# json.dumps() each list (and the as_of date), so mimic that here.
input_data = {key: json.dumps(value) for key, value in sample.items()}

namespace = {"input_data": input_data, "__name__": "zapier_code_step"}
exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), namespace)
output = namespace["output"]

(HERE / "sample_output.html").write_text(output["report_html"], encoding="utf-8")
(HERE / "sample_output.md").write_text(output["report_markdown"], encoding="utf-8")

# the sample carries a fixed as_of date, synthetic keys and last-updated stamps
assert output["title"].endswith("9 September 2026"), output["title"]
assert output["unchanged_count"] > 0 and output["stale_count"] > 0, output["changes_summary"]
assert BROWSE + "DEMO-1001" in output["report_html"]

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
assert empty["changes_summary"] == "" and empty["unchanged_count"] == 0

# --- dates: Jira formats, epochs, JSON-quoted strings, placeholders -----------
parse_timestamp = namespace["parse_timestamp"]
stamp = datetime(2026, 9, 8, 13, 3, 11, tzinfo=UTC)
assert parse_timestamp("2026-09-08T14:03:11.000+0100") == stamp          # Jira REST format
assert parse_timestamp("2026-09-08T13:03:11Z") == stamp
assert parse_timestamp("2026-09-08T14:03:11.000+01:00") == stamp
assert parse_timestamp(int(stamp.timestamp() * 1000)) == stamp           # epoch milliseconds
assert parse_timestamp(str(int(stamp.timestamp()))) == stamp             # epoch seconds as text
assert parse_timestamp("2026-09-08") == datetime(2026, 9, 8, tzinfo=UTC)
assert parse_timestamp("08/Sep/26 2:03 PM") == datetime(2026, 9, 8, 14, 3, tzinfo=UTC)   # Jira UI format
assert parse_timestamp('"2026-09-09T08:00:00+01:00"') == datetime(2026, 9, 9, 7, 0, tzinfo=UTC)  # JSON-encoded
for bad in ("undefined", "", None, "null", "not a date"):
    assert parse_timestamp(bad) is None, bad

friendly_age = namespace["friendly_age"]
now = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
assert friendly_age(now - timedelta(hours=3), now)[0] == "today"
assert friendly_age(now - timedelta(days=1), now)[0] == "yesterday"
assert friendly_age(now - timedelta(days=5), now)[0] == "5 days ago"
assert friendly_age(now - timedelta(days=22), now)[0] == "3 weeks ago"

# --- links: keys and REST "self" links become /browse/KEY links ----------------
normalise_url = namespace["normalise_url"]
normalise_key = namespace["normalise_key"]
assert normalise_url("https://checkout.atlassian.net/rest/api/3/issue/10001", "FIN-1") == BROWSE + "FIN-1"
assert normalise_url("https://api.atlassian.com/ex/jira/abc/rest/api/3/issue/FIN-2") == BROWSE + "FIN-2"
assert normalise_url("undefined", "fin-3") == BROWSE + "FIN-3"
assert normalise_url("FIN-4") == BROWSE + "FIN-4"
assert normalise_url("https://example.com/x", "FIN-5") == "https://example.com/x"
assert normalise_url("", "") == "" and normalise_url("null") == ""
assert normalise_url("https://checkout.atlassian.net/rest/api/3/issue/10001") == ""    # numeric id, key unknown
assert normalise_key("https://checkout.atlassian.net/browse/FIN-6") == "FIN-6"
assert normalise_key("fin-7") == "FIN-7" and normalise_key("nope") == "" and normalise_key(None) == ""

# --- change tracking, sorting, greying and stale dates -------------------------
as_of = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)


def ago(days):
    return (as_of - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


GREY = "font-size: 14px; color: #A0AEC0;"      # style of a greyed-out row
RED = "#C53030"
current = {
    "summary": ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"],
    "strategic_initiatives": ["SSOT"] * 6,
    "product_group": [["Ledger"]] * 6,
    "delivery_comment": ["same as before", "changed text", "brand new", "same again", "no rag change", "shipped"],
    "delivery_rag": ["On track", "On track", "Off track", "Off track", "At risk", "Done"],
    "keys": ["TST-1", "TST-2", "TST-3", "TST-4", "TST-5", "TST-6"],
    "updated": [ago(22), ago(1), ago(0), ago(2), ago(16), ago(30)],
    "as_of": as_of.isoformat(),
}
previous = {
    "keys": ["TST-1", "TST-2", "TST-4", "TST-5", "TST-6"],
    "summary": ["Alpha", "Bravo", "Delta", "Echo", "Foxtrot"],
    "delivery_comment": ["same as before", "old text", "same again", "no rag change", "shipped"],
    "delivery_rag": ["On track", "On track", "Off track", "On track", "Done"],
    "meta": {"report_date": "2026-09-02"},
}
report = build_report(dict(current, previous=json.dumps(previous)))     # previous as JSON text, like Zapier
# Alpha, Delta and Foxtrot are unchanged; Delta is Off track so it counts as an alert.
# Alpha (22 days) and Echo (16 days) are stale; Foxtrot (30 days) is Done, so it is not.
assert (report["new_count"], report["unchanged_count"], report["stale_count"]) == (1, 3, 2), report["changes_summary"]
assert report["compared_with"] == "2 Sep 2026"
assert report["changes_summary"] == ("Since the previous report (2 Sep 2026): 🆕 1 new · ✏️ 2 updated · "
                                     "⏸ 3 unchanged (1 at risk or off track) · ⚠ 2 not updated for 14+ days"), report["changes_summary"]

md = report["report_markdown"]
# urgent RAG first (unchanged Delta keeps its Off track place), greyed-out items last
positions = [md.index(f"**[{name}]") for name in ("Charlie", "Delta", "Echo", "Bravo", "Alpha", "Foxtrot")]
assert positions == sorted(positions), positions
assert "⏸ 🟢 **On track** · **[Alpha]" in md and "**⚠ Last updated 3 weeks ago (18 Aug 2026)**" in md
assert "- 🔴 **Off track** · **[Delta]" in md and "**⚠ No change since the last report**" in md
assert "⏸ ✅ **Done** · **[Foxtrot]" in md and "_(Last updated 4 weeks ago (10 Aug 2026); No change" in md

html_out = report["report_html"]
rows = {key: chunk for chunk in html_out.split("<li ") for key in current["keys"] if f"browse/{key}" in chunk}
assert len(rows) == 6
assert GREY in rows["TST-1"] and "No change since the last report" in rows["TST-1"]
assert RED in rows["TST-1"] and "⚠ Last updated 3 weeks ago (18 Aug 2026)" in rows["TST-1"]     # red, stale
assert RED in rows["TST-5"] and "Last updated 2 weeks ago" in rows["TST-5"]
assert GREY not in rows["TST-2"] and "Last updated yesterday" in rows["TST-2"]
assert "NEW" in rows["TST-3"] and "Last updated today" in rows["TST-3"]
assert f'<a href="{BROWSE}TST-3" style="color: #2B6CB0; text-decoration: none;">' in rows["TST-3"]
# Delta: unchanged but Off track -> not greyed, normal pill, red "No change" note
assert GREY not in rows["TST-4"] and "#FED7D7" in rows["TST-4"]
assert f'<span style="color: {RED}; font-size: 12px; font-weight: bold;">⚠ No change since the last report</span>' in rows["TST-4"]
# Foxtrot: Done and 30 days old -> greyed, date NOT red
assert GREY in rows["TST-6"] and "Last updated 4 weeks ago (10 Aug 2026)" in rows["TST-6"] and RED not in rows["TST-6"]
assert "⏸ No change" in html_out and "⚠ 14+ days" in html_out                                   # overview columns
assert "compared with the previous report of 2 Sep 2026" in html_out
assert "keep their place and carry a red note" in html_out and "🔥 urgency score 5" in html_out   # 2x Off track + 1x At risk

# without a previous snapshot the last-updated date decides: 7+ days = unchanged
fallback = build_report(current)
assert (fallback["new_count"], fallback["unchanged_count"], fallback["stale_count"]) == (0, 3, 2)
assert fallback["changes_summary"].startswith("Since last week:")
assert "not updated in Jira for 7 days or more" in fallback["report_html"]

# --- initiatives and product groups are ordered by urgency score ---------------
weighted = build_report({
    "summary": ["I1", "I2", "I3", "S1", "S2", "P1", "N1"],
    "strategic_initiatives": ["Issuing", "Issuing", "Issuing", "SSOT", "SSOT", "Platforms", "Non-strategic"],
    "product_group": [["Zeta"], ["Zeta"], ["Alpha"], ["Ledger"], ["Ledger"], ["Nexus"], ["Misc"]],
    "delivery_comment": ["a", "b", "c", "d", "e", "f", "g"],
    "delivery_rag": ["At risk", "At risk", "Off track", "Off track", "On track", "On track", "Off track"],
})
wmd = weighted["report_markdown"]
# scores: Issuing 4, Non-strategic 2, SSOT 2, Platforms 0 -> ties (SSOT vs Non-strategic) by item count
order = [wmd.index(f" {marker}\n_") for marker in ("Issuing", "SSOT", "Non-strategic", "Platforms")]
assert order == sorted(order), order
# within Issuing: Zeta (2 At risk = 2) ties with Alpha (1 Off track = 2) -> more items first
assert wmd.index("### Zeta") < wmd.index("### Alpha")
assert "🔥 urgency score 4" in wmd and "ordered by urgency score (Off track counts 2, At risk / Spillover counts 1)" in wmd

# no dates and no snapshot: plain report, nothing greyed out, no links
five = {key: current[key] for key in ("summary", "strategic_initiatives", "product_group", "delivery_comment", "delivery_rag")}
plain = build_report(five)
assert plain["changes_summary"] == "" and plain["unchanged_count"] == 0
assert "Last updated" not in plain["report_html"] and "browse/" not in plain["report_html"]

# keys alone give links; "undefined" links from Zapier are ignored; links can be switched off
linked = build_report(dict(five, keys=current["keys"], urls=["undefined"] * 5))
assert f'href="{BROWSE}TST-1"' in linked["report_html"]
assert f"[Alpha]({BROWSE}TST-1)" in linked["report_markdown"]
unlinked = build_report(dict(current, link_items="false"))
assert "href=" not in unlinked["report_html"] and unlinked["unchanged_count"] == 3

# previous snapshot without keys is matched by item name
by_name = build_report(dict(five, previous=json.dumps({"summary": ["Alpha"], "delivery_comment": ["same as before"],
                                                       "delivery_rag": ["On track"]})))
assert by_name["unchanged_count"] == 1 and by_name["new_count"] == 5

# --- optional urls turn item names into links (Markdown escaping) -------------
linked2 = build_report({
    "summary": ["[Delivery] Ledger", "Plain item"],
    "strategic_initiatives": ["SSOT", "SSOT"],
    "product_group": [["Ledger"], ["Ledger"]],
    "delivery_comment": ["ok", None],
    "delivery_rag": ["On track", "Done"],
    "urls": ["https://checkout.atlassian.net/browse/FIN-1", ""],
})
assert 'href="https://checkout.atlassian.net/browse/FIN-1"' in linked2["report_html"]
assert "[\\[Delivery\\] Ledger](https://checkout.atlassian.net/browse/FIN-1)" in linked2["report_markdown"]
assert "**Plain item**" in linked2["report_markdown"]

# --- importing the module must not execute the Zapier entry point -------------
sys.path.insert(0, str(HERE))
import delivery_status_report  # noqa: E402
assert not hasattr(delivery_status_report, "output")

print()
print("Output keys:", list(output))
print("Title:", output["title"])
print("RAG summary:", output["rag_summary"])
print("Changes:", output["changes_summary"])
print("Counts:", output["item_count"], "items /", output["initiative_count"], "initiatives /",
      output["product_group_count"], "product groups")
print("Warnings:", output["warnings"] or "(none)")
print("HTML size:", len(output["report_html"]), "chars; Markdown size:", len(output["report_markdown"]), "chars")
print("All checks passed.")
