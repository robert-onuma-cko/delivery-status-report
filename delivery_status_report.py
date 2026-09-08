# =============================================================================
#  Delivery Status Report -> Google Docs            (Code by Zapier, Python 3)
# =============================================================================
#  Turns five parallel lists (one entry per work item) into a nicely formatted
#  report grouped by Strategic Initiative (H2) and Product Group (H3).
#
#  INPUT DATA - map these five fields in the step's "Input Data" section:
#      summary                 work item names
#      strategic_initiatives   one strategic initiative per work item
#      product_group           one LIST of product groups per work item
#      delivery_comment        latest delivery comment per work item (may be null)
#      delivery_rag            RAG status per work item (may be null)
#
#  Each value should be a JSON array string (json.dumps(...) in the upstream
#  step). Python-style lists and Zapier's default comma-joined line items are
#  accepted as a fallback.
#
#  OPTIONAL INPUTS:
#      title                   overrides the generated document title
#      urls                    one link per work item (item names become clickable)
#
#  OUTPUT FIELDS - available to the later steps of the Zap:
#      title                 -> Google Docs "Document Name"
#      report_html           -> Google Docs "Document Content" (HTML supported)
#      report_markdown       -> the same report as plain Markdown
#      rag_summary           -> one-line RAG roll-up (handy for Slack / email)
#      item_count, initiative_count, product_group_count
#      warnings              -> data-quality notes ("" when everything lined up)
#
#  Only the Python standard library is used, so it runs unchanged in Zapier.
# =============================================================================
import ast
import html
import json
import re
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from itertools import zip_longest

# -----------------------------------------------------------------------------
# 1. CONFIGURATION - tweak freely
# -----------------------------------------------------------------------------
REPORT_TITLE = "Delivery Status Report"   # used unless a 'title' input is mapped

# Order in which strategic initiatives appear. Anything not listed is sorted
# alphabetically after these; INITIATIVES_LAST are always pushed to the bottom.
INITIATIVE_ORDER = [
    "Scale Core Acq Regions",
    "Financial Platform",
    "Faster Close",
    "SSOT",
    "Issuing",
    "MALPB",
    "Platforms",
    "SOFTPOS",
    "Consumer",
    "Regulatory compliance",
]
INITIATIVES_LAST = ["Non-strategic", "Unassigned"]

INITIATIVE_ICONS = {
    "Scale Core Acq Regions": "🌍",
    "Financial Platform": "🏦",
    "Faster Close": "⏱️",
    "SSOT": "🧭",
    "Issuing": "💳",
    "MALPB": "🏛️",
    "Platforms": "🧩",
    "SOFTPOS": "📱",
    "Consumer": "👤",
    "Regulatory compliance": "⚖️",
    "Non-strategic": "🧰",
}
DEFAULT_INITIATIVE_ICON = "📂"

# RAG statuses: display label, emoji, text colour, highlight colour and the
# order items are listed in (lower rank = shown first = needs attention).
RAG_STYLES = OrderedDict([
    ("off_track", {"label": "Off track",           "emoji": "🔴", "color": "#822727", "background": "#FED7D7", "rank": 0}),
    ("at_risk",   {"label": "At risk / Spillover", "emoji": "🟠", "color": "#7B341E", "background": "#FEEBC8", "rank": 1}),
    ("on_track",  {"label": "On track",            "emoji": "🟢", "color": "#22543D", "background": "#C6F6D5", "rank": 2}),
    ("planned",   {"label": "Planned",             "emoji": "🔵", "color": "#2A4365", "background": "#BEE3F8", "rank": 3}),
    ("no_rag",    {"label": "No RAG",              "emoji": "⚫", "color": "#4A5568", "background": "#EDF2F7", "rank": 4}),
    ("done",      {"label": "Done",                "emoji": "✅", "color": "#234E52", "background": "#B2F5EA", "rank": 5}),
    ("dropped",   {"label": "Dropped",             "emoji": "⚪", "color": "#718096", "background": "#E2E8F0", "rank": 6}),
])
# Lower-case source values -> RAG_STYLES key. Unrecognised values are shown
# verbatim with the neutral "no_rag" styling and counted as "Other".
RAG_ALIASES = {
    "off track": "off_track", "blocked": "off_track", "red": "off_track", "delayed": "off_track",
    "at risk / spillover": "at_risk", "at risk": "at_risk", "spillover": "at_risk", "amber": "at_risk",
    "on track": "on_track", "in progress": "on_track", "green": "on_track",
    "planned": "planned", "not started": "planned", "backlog": "planned", "to do": "planned",
    "done": "done", "complete": "done", "completed": "done", "delivered": "done",
    "dropped": "dropped", "descoped": "dropped", "cancelled": "dropped", "canceled": "dropped",
}
UNKNOWN_RAG_KEY = "unknown"

SORT_ITEMS_BY_RAG = True          # False = keep the order the items arrived in
REPEAT_MULTI_GROUP_ITEMS = True   # True = an item with two product groups is listed under both
DEDUPE_EXACT_DUPLICATES = True    # drop rows that are identical in all five fields
INCLUDE_OVERVIEW_TABLE = True     # RAG roll-up table by initiative at the top of the doc
UNASSIGNED_LABEL = "Unassigned"
NO_COMMENT_TEXT = "No update provided."

# -----------------------------------------------------------------------------
# 2. GOOGLE DOCS FRIENDLY INLINE STYLES
# -----------------------------------------------------------------------------
FONT = "font-family: Arial, Helvetica, sans-serif;"
WRAPPER_STYLE = FONT + " color: #2D3748; line-height: 1.6;"
H1_STYLE = FONT + " color: #1A202C; font-size: 26px; margin-bottom: 4px;"
SUBTITLE_STYLE = FONT + " color: #718096; font-size: 13px; margin-top: 0; margin-bottom: 14px;"
H2_STYLE = (FONT + " color: #1A202C; font-size: 20px; margin-top: 30px; margin-bottom: 2px;"
            " border-bottom: 2px solid #E2E8F0; padding-bottom: 6px;")
H2_META_STYLE = FONT + " color: #718096; font-size: 12px; margin-top: 0; margin-bottom: 8px;"
H3_STYLE = FONT + " color: #2B6CB0; font-size: 16px; margin-top: 18px; margin-bottom: 4px;"
LIST_STYLE = "padding-left: 22px; margin-top: 2px; margin-bottom: 12px;"
LI_STYLE = "margin-bottom: 6px; font-size: 14px;"
COMMENT_STYLE = "color: #4A5568;"
MUTED_STYLE = "color: #A0AEC0; font-style: italic;"
SMALL_MUTED_STYLE = "color: #A0AEC0; font-size: 12px;"
DROPPED_NAME_STYLE = "color: #A0AEC0; text-decoration: line-through;"
LINK_STYLE = "color: #2B6CB0; text-decoration: none; font-weight: bold;"
NAME_LINK_STYLE = "color: #1A202C; text-decoration: none;"
HR_STYLE = "border: 0; border-top: 1px solid #E2E8F0; margin-top: 22px; margin-bottom: 22px;"
PILL_STYLE = "font-size: 12px; font-weight: bold; color: {color}; background-color: {background};"
TABLE_STYLE = "border-collapse: collapse; font-size: 13px; margin-top: 8px; margin-bottom: 8px;"
TH_STYLE = "border: 1px solid #E2E8F0; padding: 4px 10px; background-color: #F7FAFC; text-align: left; font-weight: bold;"
TD_STYLE = "border: 1px solid #E2E8F0; padding: 4px 10px;"
TD_NUM_STYLE = TD_STYLE + " text-align: center;"


# -----------------------------------------------------------------------------
# 3. INPUT PARSING & NORMALISATION
# -----------------------------------------------------------------------------
def clean_text(value):
    """str() a value, trim it and normalise whitespace (paragraph breaks are kept)."""
    if value is None:
        return ""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(value).splitlines()]
    return "\n".join(line for line in lines if line)


def plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def parse_list(raw, field_name, warnings):
    """Turn a Zapier input value into a list: JSON -> Python literal -> comma split."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    text = str(raw).strip()
    if not text:
        return []
    for loader in (json.loads, ast.literal_eval):
        try:
            value = loader(text)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
        if isinstance(value, str):                 # double-encoded JSON string
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]
    warnings.append(f"'{field_name}' was not a JSON list, so it was split on commas.")
    return [part.strip() for part in text.split(",")]


def normalise_groups(value):
    """Return a de-duplicated list of product group names for one work item."""
    if isinstance(value, str):
        text = value.strip()
        parsed = None
        if text.startswith("["):
            for loader in (json.loads, ast.literal_eval):
                try:
                    parsed = loader(text)
                    break
                except (ValueError, SyntaxError, TypeError):
                    continue
        value = parsed if isinstance(parsed, (list, tuple)) else [text]
    elif isinstance(value, dict):
        value = [value]
    elif not isinstance(value, (list, tuple)):
        value = [value]

    groups = []
    for entry in value:
        if isinstance(entry, dict):                # e.g. {"name": "Ledger"} from Jira/Atlas
            entry = entry.get("name") or entry.get("value") or ""
        name = clean_text(entry)
        if name and name not in groups:
            groups.append(name)
    return groups or [UNASSIGNED_LABEL]


def resolve_rag(raw):
    """Map a raw RAG value to (style_key, display_label)."""
    text = clean_text(raw)
    if not text:
        return "no_rag", RAG_STYLES["no_rag"]["label"]
    key = RAG_ALIASES.get(text.lower())
    if key:
        return key, RAG_STYLES[key]["label"]
    return UNKNOWN_RAG_KEY, text


def rag_style(key):
    return RAG_STYLES.get(key, RAG_STYLES["no_rag"])


def rag_rank(key):
    if key in RAG_STYLES:
        return RAG_STYLES[key]["rank"]
    return RAG_STYLES["no_rag"]["rank"] + 0.5       # unknown values sit next to "No RAG"


def rag_display_label(key):
    return RAG_STYLES[key]["label"] if key in RAG_STYLES else "Other"


def initiative_icon(name):
    for known, icon in INITIATIVE_ICONS.items():
        if known.lower() == name.lower():
            return icon
    return DEFAULT_INITIATIVE_ICON


# -----------------------------------------------------------------------------
# 4. BUILD, GROUP AND COUNT THE WORK ITEMS
# -----------------------------------------------------------------------------
def build_items(summaries, initiatives, groups, comments, rags, warnings, urls=None):
    lengths = OrderedDict([
        ("summary", len(summaries)),
        ("strategic_initiatives", len(initiatives)),
        ("product_group", len(groups)),
        ("delivery_comment", len(comments)),
        ("delivery_rag", len(rags)),
    ])
    if len(set(lengths.values())) > 1:
        detail = ", ".join(f"{name}={count}" for name, count in lengths.items())
        warnings.append(f"Input lists have different lengths ({detail}); rows were aligned by "
                        "position and missing values treated as blank.")

    urls = list(urls or [])[:max(lengths.values())]      # optional links never add rows
    items, seen, duplicates = [], set(), 0
    rows = zip_longest(summaries, initiatives, groups, comments, rags, urls, fillvalue=None)
    for index, (name, initiative, group, comment, rag, url) in enumerate(rows):
        rag_key, rag_label = resolve_rag(rag)
        item = {
            "order": index,
            "name": clean_text(name) or f"Untitled work item #{index + 1}",
            "initiative": clean_text(initiative) or UNASSIGNED_LABEL,
            "groups": normalise_groups(group),
            "comment": clean_text(comment),
            "rag_key": rag_key,
            "rag_label": rag_label,
            "url": clean_text(url),
        }
        fingerprint = (
            item["name"].lower(),
            item["initiative"].lower(),
            tuple(g.lower() for g in item["groups"]),
            item["comment"].lower(),
            rag_label.lower(),
        )
        if DEDUPE_EXACT_DUPLICATES and fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        items.append(item)

    if duplicates:
        warnings.append(f"{plural(duplicates, 'exact duplicate row')} removed.")
    return items


def initiative_sort_key(name):
    lowered = name.lower()
    first = [n.lower() for n in INITIATIVE_ORDER]
    last = [n.lower() for n in INITIATIVES_LAST]
    if lowered in last:
        return (2, last.index(lowered), lowered)
    if lowered in first:
        return (0, first.index(lowered), lowered)
    return (1, 0, lowered)


def group_items(items):
    """initiative -> product group -> [items], ordered and (optionally) RAG-sorted."""
    tree = {}
    for item in items:
        groups = item["groups"] if REPEAT_MULTI_GROUP_ITEMS else [" / ".join(item["groups"])]
        for group in groups:
            tree.setdefault(item["initiative"], {}).setdefault(group, []).append(item)

    ordered = OrderedDict()
    for initiative in sorted(tree, key=initiative_sort_key):
        ordered[initiative] = OrderedDict()
        for group in sorted(tree[initiative], key=lambda g: (g == UNASSIGNED_LABEL, g.lower())):
            bucket = tree[initiative][group]
            if SORT_ITEMS_BY_RAG:
                bucket = sorted(bucket, key=lambda it: (rag_rank(it["rag_key"]), it["order"]))
            ordered[initiative][group] = bucket
    return ordered


def compute_stats(items, tree):
    per_initiative = {}
    for item in items:
        per_initiative.setdefault(item["initiative"], Counter())[item["rag_key"]] += 1
    return {
        "item_count": len(items),
        "initiative_count": len(tree),
        "product_group_count": len({g for item in items for g in item["groups"]}),
        "rag_totals": Counter(item["rag_key"] for item in items),
        "per_initiative": per_initiative,
    }


def rag_keys_in_use(counter):
    """RAG keys present in a Counter, in display (rank) order."""
    keys = [key for key in RAG_STYLES if counter.get(key)]
    if counter.get(UNKNOWN_RAG_KEY):
        keys.append(UNKNOWN_RAG_KEY)
    return keys


def rag_counts_text(counter, separator=" · "):
    """e.g. '🟢 On track 20 · ✅ Done 8'"""
    chunks = []
    for key in rag_keys_in_use(counter):
        chunks.append(f"{rag_style(key)['emoji']} {rag_display_label(key)} {counter[key]}")
    return separator.join(chunks)


# -----------------------------------------------------------------------------
# 5. HTML RENDERING (what goes into the Google Doc)
# -----------------------------------------------------------------------------
def esc(text):
    return html.escape(str(text), quote=True)


def rich_html(text):
    """Escape text, then honour **bold** and [label](https://url) written in comments."""
    out = esc(text)
    out = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: '<a href="' + m.group(2) + '" style="' + LINK_STYLE + '">' + m.group(1) + "</a>",
        out,
    )
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out.replace("\n", "<br>")


def pill_html(key, label, count=None):
    style = rag_style(key)
    css = PILL_STYLE.format(color=style["color"], background=style["background"])
    text = style["emoji"] + " " + esc(label)
    if count is not None:
        text += f": {count}"
    return f'<span style="{css}">&nbsp;{text}&nbsp;</span>'


def item_html(item, current_group):
    pill = pill_html(item["rag_key"], item["rag_label"])

    name_html = rich_html(item["name"])
    if item["rag_key"] == "dropped":
        name_html = f'<span style="{DROPPED_NAME_STYLE}">{name_html}</span>'
    name_html = f"<strong>{name_html}</strong>"
    if item.get("url"):
        href = esc(item["url"])
        name_html = f'<a href="{href}" style="{NAME_LINK_STYLE}">{name_html}</a>'

    shared_html = ""
    others = [g for g in item["groups"] if g != current_group]
    if others:
        others_text = esc(", ".join(others))
        shared_html = f' <span style="{SMALL_MUTED_STYLE}">(also under {others_text})</span>'

    if item["comment"]:
        comment_body = rich_html(item["comment"])
        comment_html = f'<span style="{COMMENT_STYLE}">{comment_body}</span>'
    else:
        comment_html = f'<span style="{MUTED_STYLE}">{esc(NO_COMMENT_TEXT)}</span>'

    return f'<li style="{LI_STYLE}">{pill} {name_html}{shared_html} — {comment_html}</li>'


def overview_table_html(tree, stats):
    keys = rag_keys_in_use(stats["rag_totals"])
    head = [f'<th style="{TH_STYLE}">Strategic initiative</th>', f'<th style="{TH_STYLE}">Items</th>']
    for key in keys:
        label = rag_style(key)["emoji"] + " " + esc(rag_display_label(key))
        head.append(f'<th style="{TH_STYLE}">{label}</th>')

    rows = []
    for initiative in tree:
        counter = stats["per_initiative"][initiative]
        cells = [
            f'<td style="{TD_STYLE}">{initiative_icon(initiative)} {esc(initiative)}</td>',
            f'<td style="{TD_NUM_STYLE}"><strong>{sum(counter.values())}</strong></td>',
        ]
        for key in keys:
            value = counter.get(key) or "–"
            cells.append(f'<td style="{TD_NUM_STYLE}">{value}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    total_cells = [
        f'<td style="{TD_STYLE}"><strong>Total</strong></td>',
        f'<td style="{TD_NUM_STYLE}"><strong>{stats["item_count"]}</strong></td>',
    ]
    for key in keys:
        total_cells.append(f'<td style="{TD_NUM_STYLE}"><strong>{stats["rag_totals"][key]}</strong></td>')
    rows.append("<tr>" + "".join(total_cells) + "</tr>")

    return (f'<table style="{TABLE_STYLE}"><thead><tr>' + "".join(head) + "</tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


def footer_text():
    note = "Items are sorted by RAG status within each product group. " if SORT_ITEMS_BY_RAG else ""
    return note + "Generated automatically by Zapier."


def render_html(title, subtitle, tree, stats):
    parts = [f'<div style="{WRAPPER_STYLE}">']
    parts.append(f'<h1 style="{H1_STYLE}">📋 {esc(title)}</h1>')
    parts.append(f'<p style="{SUBTITLE_STYLE}">{esc(subtitle)}</p>')

    if stats["item_count"]:
        pills = " &nbsp;".join(
            pill_html(key, rag_display_label(key), stats["rag_totals"][key])
            for key in rag_keys_in_use(stats["rag_totals"])
        )
        parts.append(f'<p style="{LI_STYLE}">{pills}</p>')
        if INCLUDE_OVERVIEW_TABLE:
            parts.append(overview_table_html(tree, stats))
    else:
        parts.append(f'<p style="{MUTED_STYLE}">No work items were supplied.</p>')

    parts.append(f'<hr style="{HR_STYLE}">')

    for initiative, groups in tree.items():
        counter = stats["per_initiative"][initiative]
        meta = plural(sum(counter.values()), "work item") + " · " + rag_counts_text(counter)
        parts.append(f'<h2 style="{H2_STYLE}">{initiative_icon(initiative)} {esc(initiative)}</h2>')
        parts.append(f'<p style="{H2_META_STYLE}">{esc(meta)}</p>')
        for group, bucket in groups.items():
            count_html = f'<span style="{SMALL_MUTED_STYLE}">({len(bucket)})</span>'
            parts.append(f'<h3 style="{H3_STYLE}">{esc(group)} {count_html}</h3>')
            parts.append(f'<ul style="{LIST_STYLE}">')
            parts.extend(item_html(item, group) for item in bucket)
            parts.append("</ul>")

    parts.append(f'<hr style="{HR_STYLE}">')
    parts.append(f'<p style="{SMALL_MUTED_STYLE}">{esc(footer_text())}</p>')
    parts.append("</div>")
    return "\n".join(parts)


# -----------------------------------------------------------------------------
# 6. MARKDOWN RENDERING (same content, plain Markdown)
# -----------------------------------------------------------------------------
def md_escape(text):
    return re.sub(r"([*_`|~])", r"\\\1", str(text))


def item_md(item, current_group):
    style = rag_style(item["rag_key"])
    name = md_escape(item["name"])
    if item.get("url"):
        link_text = name.replace("[", "\\[").replace("]", "\\]")
        name = f"[{link_text}]({item['url']})"
    if item["rag_key"] == "dropped":
        name = f"~~{name}~~"
    shared = ""
    others = [g for g in item["groups"] if g != current_group]
    if others:
        shared = " _(also under " + md_escape(", ".join(others)) + ")_"
    comment = item["comment"].replace("\n", " ") if item["comment"] else f"_{NO_COMMENT_TEXT}_"
    return f"- {style['emoji']} **{md_escape(item['rag_label'])}** · **{name}**{shared} — {comment}"


def overview_table_md(tree, stats):
    keys = rag_keys_in_use(stats["rag_totals"])
    header = ["Strategic initiative", "Items"] + [rag_style(k)["emoji"] + " " + rag_display_label(k) for k in keys]
    lines = ["| " + " | ".join(header) + " |", "|:--|" + "--:|" * (len(header) - 1)]
    for initiative in tree:
        counter = stats["per_initiative"][initiative]
        cells = [initiative_icon(initiative) + " " + md_escape(initiative), str(sum(counter.values()))]
        cells += [str(counter.get(k) or "–") for k in keys]
        lines.append("| " + " | ".join(cells) + " |")
    totals = ["**Total**", f"**{stats['item_count']}**"] + [f"**{stats['rag_totals'][k]}**" for k in keys]
    lines.append("| " + " | ".join(totals) + " |")
    return lines


def render_markdown(title, subtitle, tree, stats):
    lines = [f"# 📋 {md_escape(title)}", f"_{subtitle}_", ""]
    if stats["item_count"]:
        lines += ["**RAG roll-up:** " + rag_counts_text(stats["rag_totals"]), ""]
        if INCLUDE_OVERVIEW_TABLE:
            lines += overview_table_md(tree, stats) + [""]
    else:
        lines += ["_No work items were supplied._", ""]
    lines.append("---")

    for initiative, groups in tree.items():
        counter = stats["per_initiative"][initiative]
        meta = plural(sum(counter.values()), "work item") + " · " + rag_counts_text(counter)
        lines += ["", f"## {initiative_icon(initiative)} {md_escape(initiative)}", f"_{meta}_"]
        for group, bucket in groups.items():
            lines += ["", f"### {md_escape(group)} ({len(bucket)})", ""]
            lines.extend(item_md(item, group) for item in bucket)

    lines += ["", "---", f"_{footer_text()}_"]
    return "\n".join(lines).strip() + "\n"


# -----------------------------------------------------------------------------
# 7. ENTRY POINT
# -----------------------------------------------------------------------------
def build_report(data):
    warnings = []
    summaries = parse_list(data.get("summary"), "summary", warnings)
    initiatives = parse_list(data.get("strategic_initiatives"), "strategic_initiatives", warnings)
    groups = parse_list(data.get("product_group"), "product_group", warnings)
    comments = parse_list(data.get("delivery_comment"), "delivery_comment", warnings)
    rags = parse_list(data.get("delivery_rag"), "delivery_rag", warnings)
    urls = parse_list(data.get("urls"), "urls", [])         # optional: one link per work item

    items = build_items(summaries, initiatives, groups, comments, rags, warnings, urls)
    if not items:
        warnings.append("No work items were found in the input data.")
    tree = group_items(items)
    stats = compute_stats(items, tree)

    today = datetime.now(timezone.utc)
    date_text = f"{today.day} {today:%B %Y}"
    title = clean_text(data.get("title")) or f"{REPORT_TITLE} – {date_text}"
    subtitle = (
        f"Generated {today:%A} {date_text} · "
        + plural(stats["item_count"], "work item") + " across "
        + plural(stats["initiative_count"], "strategic initiative") + " and "
        + plural(stats["product_group_count"], "product group")
    )

    return {
        "title": title,
        "report_html": render_html(title, subtitle, tree, stats),
        "report_markdown": render_markdown(title, subtitle, tree, stats),
        "rag_summary": rag_counts_text(stats["rag_totals"]) or "No RAG data",
        "item_count": stats["item_count"],
        "initiative_count": stats["initiative_count"],
        "product_group_count": stats["product_group_count"],
        "warnings": " | ".join(warnings),
    }


# `input_data` is injected by Zapier. When this file is imported as a module
# (run_weekly_report.py does that) there is no input_data and nothing below runs.
try:
    input_data
except NameError:
    input_data = None

if input_data is not None:
    output = build_report(input_data)
    print(f"Report '{output['title']}': {output['item_count']} items, "
          f"{output['initiative_count']} initiatives, {output['product_group_count']} product groups.")
    if output["warnings"]:
        print("Warnings: " + output["warnings"])
