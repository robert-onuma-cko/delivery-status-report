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
#      keys                    Jira issue key per work item (e.g. FIN-123); the item
#                              name becomes a link to https://checkout.atlassian.net/browse/KEY
#      urls                    one link per work item. Jira REST "self" links and
#                              "undefined" values are replaced by the /browse/KEY link.
#      updated                 last-updated timestamp per work item (Jira "updated"
#                              field). Shown as "Last updated 3 days ago", in red once
#                              older than STALE_AFTER_DAYS.
#      previous                last week's items as JSON (the same lists: keys, summary,
#                              delivery_comment, delivery_rag). Items whose comment and
#                              RAG did not change are greyed out and moved to the bottom
#                              of their group. Without it, items not updated for
#                              UNCHANGED_AFTER_DAYS count as unchanged.
#      as_of                   report date/time (ISO 8601) - defaults to now
#
#  OUTPUT FIELDS - available to the later steps of the Zap:
#      title                 -> Google Docs "Document Name"
#      report_html           -> Google Docs "Document Content" (HTML supported)
#      report_markdown       -> the same report as plain Markdown
#      rag_summary           -> one-line RAG roll-up (handy for Slack / email)
#      changes_summary       -> "Since last week: 3 new · 20 updated · 12 unchanged ..."
#      item_count, initiative_count, product_group_count
#      new_count, unchanged_count, stale_count
#      warnings              -> data-quality notes ("" when everything lined up)
#
#  Only the Python standard library is used, so it runs unchanged in Zapier.
# =============================================================================
import ast
import html
import json
import re
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
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

SORT_ITEMS_BY_RAG = True          # most urgent RAG first within each product group
UNCHANGED_TO_BOTTOM = True        # items with no change since the last report are greyed out and listed last
REPEAT_MULTI_GROUP_ITEMS = True   # True = an item with two product groups is listed under both
DEDUPE_EXACT_DUPLICATES = True    # drop rows that are identical in all five fields
INCLUDE_OVERVIEW_TABLE = True     # RAG roll-up table by initiative at the top of the doc
UNASSIGNED_LABEL = "Unassigned"
NO_COMMENT_TEXT = "No update provided."
UNCHANGED_TEXT = "No change since the last report"

# Work item names link to Jira. Keys come from the 'keys' input or are read out of
# the 'urls' input (REST API "self" links are never shown to readers).
JIRA_BROWSE_URL = "https://checkout.atlassian.net/browse/{key}"

STALE_AFTER_DAYS = 14             # "Last updated" turns red when the item is older than this
UNCHANGED_AFTER_DAYS = 7          # no 'previous' snapshot: not updated for a week = no change
DISPLAY_TIMEZONE = "Europe/London"   # for dates in the report (falls back to UTC)

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
NAME_LINK_STYLE = "color: #2B6CB0; text-decoration: none;"
UNCHANGED_STYLE = "color: #A0AEC0;"
UNCHANGED_LINK_STYLE = "color: #A0AEC0; text-decoration: none;"
UNCHANGED_PILL_STYLE = "font-size: 12px; font-weight: bold; color: #A0AEC0; background-color: #F7FAFC;"
UPDATED_STYLE = "color: #A0AEC0; font-size: 12px;"
STALE_STYLE = "color: #C53030; font-size: 12px; font-weight: bold;"
NEW_TAG_STYLE = "font-size: 11px; font-weight: bold; color: #2A4365; background-color: #BEE3F8;"
CHANGES_STYLE = FONT + " color: #4A5568; font-size: 13px; margin-top: 4px; margin-bottom: 10px;"
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
# 3b. DATES, JIRA LINKS AND LAST WEEK'S SNAPSHOT
# -----------------------------------------------------------------------------
MISSING_VALUES = {"", "undefined", "null", "none", "nan", "n/a", "-", "[]", "{}"}
ISSUE_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]+-\d+")
URL_KEY_RE = re.compile(r"/(?:browse|issues?)/([A-Za-z][A-Za-z0-9_]+-\d+)")


def is_missing(value):
    """True for blanks and the placeholders Zapier / JavaScript produce for absent values."""
    return clean_text(value).strip("\"'").lower() in MISSING_VALUES


def display_timezone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(DISPLAY_TIMEZONE)
    except Exception:  # noqa: BLE001 - zone database unavailable (e.g. Windows without tzdata)
        return timezone.utc


def parse_timestamp(value):
    """Best-effort date/time parsing to an aware UTC datetime; None when unreadable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)):
        seconds = value / 1000 if abs(value) > 1e11 else value      # epoch milliseconds vs seconds
        try:
            moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = clean_text(value).strip("\"'")
        if is_missing(text):
            return None
        if re.fullmatch(r"\d{9,}", text):                              # epoch as a string
            return parse_timestamp(int(text))
        iso = re.sub(r"[zZ]$", "+00:00", text)
        iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)            # Jira's +0100 -> +01:00
        iso = re.sub(r"(\.\d{1,6})\d+", r"\1", iso)                    # at most microseconds
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
            iso += "T00:00:00"
        moment = None
        try:
            moment = datetime.fromisoformat(iso)
        except ValueError:
            for pattern in ("%d/%b/%y %I:%M %p", "%d/%b/%Y %I:%M %p", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                            "%d %b %Y %H:%M", "%d %b %Y", "%b %d, %Y %I:%M %p", "%b %d, %Y", "%Y/%m/%d"):
                try:
                    moment = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if moment is None:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def friendly_age(moment, now):
    """How long ago `moment` was, in calendar days: ('3 days ago', 3)."""
    zone = display_timezone()
    days = (now.astimezone(zone).date() - moment.astimezone(zone).date()).days
    if days <= 0:
        text = "today"
    elif days == 1:
        text = "yesterday"
    elif days < 14:
        text = f"{days} days ago"
    elif days < 60:
        text = plural(days // 7, "week") + " ago"
    elif days < 365:
        text = plural(max(2, round(days / 30.44)), "month") + " ago"
    else:
        text = plural(max(1, round(days / 365.25)), "year") + " ago"
    return text, days


def short_date(moment):
    local = moment.astimezone(display_timezone())
    return f"{local.day} {local:%b %Y}"


def normalise_key(value):
    """'fin-123', 'FIN-123' or a Jira URL containing the key -> 'FIN-123' ('' when absent)."""
    text = clean_text(value).strip("\"'")
    if is_missing(text):
        return ""
    if ISSUE_KEY_RE.fullmatch(text.upper()):
        return text.upper()
    match = URL_KEY_RE.search(text)
    return match.group(1).upper() if match else ""


def normalise_url(url, key=""):
    """A link readers can click: /browse/KEY from the key, or the given URL unless it is a REST link."""
    key = normalise_key(key)
    text = "" if is_missing(url) else clean_text(url).strip("\"'")
    if text and not re.match(r"https?://", text, re.I):           # e.g. a bare key was mapped as the link
        key = key or normalise_key(text)
        text = ""
    if re.search(r"/rest/api/|api\.atlassian\.com", text, re.I):   # REST "self" link, useless to readers
        key = key or normalise_key(text)
        text = ""
    if text:
        return text
    return JIRA_BROWSE_URL.format(key=key) if key else ""


def parse_previous(raw, warnings):
    """Last report's items -> {'by_key': {...}, 'by_name': {...}, 'label': '2 Sep 2026'}, or None."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    data = raw
    if isinstance(raw, str):
        if is_missing(raw):
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            warnings.append("'previous' was not valid JSON, so unchanged items could not be detected.")
            return None
    names = ("keys", "summary", "delivery_comment", "delivery_rag")
    label_raw, rows = "", []
    if isinstance(data, dict):
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        label_raw = data.get("report_date") or meta.get("report_date") or meta.get("fetched_at") or data.get("as_of")
        if isinstance(data.get("items"), list):
            rows = data["items"]
        else:
            lists = [parse_list(data.get(name), name, []) for name in names]
            rows = [dict(zip(("key",) + names[1:], values)) for values in zip_longest(*lists, fillvalue=None)]
    elif isinstance(data, list):
        rows = data
    by_key, by_name = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = (clean_text(row.get("delivery_comment", row.get("comment"))).lower(),
                 resolve_rag(row.get("delivery_rag", row.get("rag")))[1].lower())
        key = normalise_key(row.get("key"))
        name = clean_text(row.get("summary", row.get("name"))).lower()
        if key:
            by_key[key] = state
        if name:
            by_name.setdefault(name, state)
    if not by_key and not by_name:
        return None
    moment = parse_timestamp(label_raw)
    return {"by_key": by_key, "by_name": by_name, "label": short_date(moment) if moment else clean_text(label_raw)}


# -----------------------------------------------------------------------------
# 4. BUILD, GROUP AND COUNT THE WORK ITEMS
# -----------------------------------------------------------------------------
def build_items(summaries, initiatives, groups, comments, rags, warnings, urls=None, keys=None, updated=None):
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

    limit = max(lengths.values())
    urls = list(urls or [])[:limit]                  # optional per-item extras never add rows
    keys = list(keys or [])[:limit]
    updated = list(updated or [])[:limit]
    items, seen, duplicates, unreadable = [], set(), 0, 0
    rows = zip_longest(summaries, initiatives, groups, comments, rags, urls, keys, updated, fillvalue=None)
    for index, (name, initiative, group, comment, rag, url, key, stamp) in enumerate(rows):
        rag_key, rag_label = resolve_rag(rag)
        key = normalise_key(key) or normalise_key(url)
        moment = parse_timestamp(stamp)
        if moment is None and not is_missing(stamp):
            unreadable += 1
        item = {
            "order": index,
            "name": clean_text(name) or f"Untitled work item #{index + 1}",
            "initiative": clean_text(initiative) or UNASSIGNED_LABEL,
            "groups": normalise_groups(group),
            "comment": clean_text(comment),
            "rag_key": rag_key,
            "rag_label": rag_label,
            "key": key,
            "url": normalise_url(url, key),
            "updated": moment,
            "age_text": "",
            "age_days": None,
            "stale": False,
            "change": None,        # 'new' / 'updated' / 'unchanged' once mark_changes() has run
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
    if unreadable:
        noun = "'updated' value"
        warnings.append(f"{plural(unreadable, noun)} could not be read as a date.")
    return items


def mark_changes(items, previous, now):
    """Fill in change ('new' / 'updated' / 'unchanged' / None), stale and age for every item."""
    stale_after = timedelta(days=STALE_AFTER_DAYS)
    unchanged_after = timedelta(days=UNCHANGED_AFTER_DAYS)
    for item in items:
        moment = item["updated"]
        if moment is not None:
            item["age_text"], item["age_days"] = friendly_age(moment, now)
            item["stale"] = now - moment > stale_after
        state = (item["comment"].lower(), item["rag_label"].lower())
        if previous:
            old = previous["by_key"].get(item["key"]) if item["key"] else None
            if old is None:
                old = previous["by_name"].get(item["name"].lower())
            if old is None:
                item["change"] = "new"
            else:
                item["change"] = "unchanged" if old == state else "updated"
        elif moment is not None:
            item["change"] = "unchanged" if now - moment >= unchanged_after else "updated"
    return items


def item_sort_key(item):
    """Unchanged items last (greyed out), then most urgent RAG first, then input order."""
    unchanged = 1 if UNCHANGED_TO_BOTTOM and item.get("change") == "unchanged" else 0
    rag = rag_rank(item["rag_key"]) if SORT_ITEMS_BY_RAG else 0
    return (unchanged, rag, item["order"])


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
            if SORT_ITEMS_BY_RAG or UNCHANGED_TO_BOTTOM:
                bucket = sorted(bucket, key=item_sort_key)
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
        "new_count": sum(1 for item in items if item.get("change") == "new"),
        "unchanged_count": sum(1 for item in items if item.get("change") == "unchanged"),
        "stale_count": sum(1 for item in items if item.get("stale")),
        "tracking": any(item.get("change") for item in items),          # change detection was possible
        "has_dates": any(item.get("updated") for item in items),
        "unchanged_per_initiative": Counter(item["initiative"] for item in items if item.get("change") == "unchanged"),
        "stale_per_initiative": Counter(item["initiative"] for item in items if item.get("stale")),
        "compared_with": "",       # label of the previous report, filled in by build_report()
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


def pill_html(key, label, count=None, muted=False):
    style = rag_style(key)
    css = UNCHANGED_PILL_STYLE if muted else PILL_STYLE.format(color=style["color"], background=style["background"])
    text = style["emoji"] + " " + esc(label)
    if count is not None:
        text += f": {count}"
    return f'<span style="{css}">&nbsp;{text}&nbsp;</span>'


def tag_html(text, css):
    return f'<span style="{css}">&nbsp;{esc(text)}&nbsp;</span>'


def updated_text(item):
    return f"Last updated {item['age_text']} ({short_date(item['updated'])})" if item.get("updated") else ""


def updated_html(item):
    """'Last updated 3 days ago (6 Sep 2026)', in red once older than STALE_AFTER_DAYS."""
    text = updated_text(item)
    if not text:
        return ""
    if item.get("stale"):
        return f'<span style="{STALE_STYLE}">⚠ {esc(text)}</span>'
    return f'<span style="{UPDATED_STYLE}">{esc(text)}</span>'


def initiative_meta(initiative, stats):
    counter = stats["per_initiative"][initiative]
    text = plural(sum(counter.values()), "work item") + " · " + rag_counts_text(counter)
    unchanged = stats["unchanged_per_initiative"].get(initiative)
    stale = stats["stale_per_initiative"].get(initiative)
    if unchanged:
        text += f" · ⏸ {unchanged} unchanged"
    if stale:
        text += f" · ⚠ {stale} not updated for {STALE_AFTER_DAYS}+ days"
    return text


def changes_text(stats):
    """One line about what moved since the last report; '' when there is nothing to compare."""
    parts = []
    if stats.get("tracking"):
        updated = stats["item_count"] - stats["new_count"] - stats["unchanged_count"]
        if stats["new_count"]:
            parts.append(f"🆕 {stats['new_count']} new")
        parts.append(f"✏️ {updated} updated")
        parts.append(f"⏸ {stats['unchanged_count']} unchanged")
    if stats.get("has_dates"):
        parts.append(f"⚠ {stats['stale_count']} not updated for {STALE_AFTER_DAYS}+ days")
    if not parts:
        return ""
    if stats.get("tracking"):
        since = "Since the previous report" if stats.get("snapshot") else "Since last week"
        if stats.get("compared_with"):
            since += f" ({stats['compared_with']})"
        return since + ": " + " · ".join(parts)
    return " · ".join(parts)


def extra_columns(stats):
    """Change-tracking columns for the overview table: (header, per-initiative counts, total)."""
    columns = []
    if stats.get("tracking"):
        columns.append(("⏸ No change", stats["unchanged_per_initiative"], stats["unchanged_count"]))
    if stats.get("has_dates"):
        columns.append((f"⚠ {STALE_AFTER_DAYS}+ days", stats["stale_per_initiative"], stats["stale_count"]))
    return columns


def item_html(item, current_group):
    unchanged = item.get("change") == "unchanged"
    pill = pill_html(item["rag_key"], item["rag_label"], muted=unchanged)

    name_html = rich_html(item["name"])
    if item["rag_key"] == "dropped":
        name_html = f'<span style="{DROPPED_NAME_STYLE}">{name_html}</span>'
    name_html = f"<strong>{name_html}</strong>"
    if item.get("url"):
        href = esc(item["url"])
        link_style = UNCHANGED_LINK_STYLE if unchanged else NAME_LINK_STYLE
        name_html = f'<a href="{href}" style="{link_style}">{name_html}</a>'
    if item.get("change") == "new":
        name_html += " " + tag_html("NEW", NEW_TAG_STYLE)

    shared_html = ""
    others = [g for g in item["groups"] if g != current_group]
    if others:
        others_text = esc(", ".join(others))
        shared_html = f' <span style="{SMALL_MUTED_STYLE}">(also under {others_text})</span>'

    if item["comment"]:
        comment_style = UNCHANGED_STYLE if unchanged else COMMENT_STYLE
        comment_html = f'<span style="{comment_style}">{rich_html(item["comment"])}</span>'
    else:
        comment_html = f'<span style="{MUTED_STYLE}">{esc(NO_COMMENT_TEXT)}</span>'

    notes = [updated_html(item)]
    if unchanged:
        notes.append(f'<span style="{UPDATED_STYLE}">{esc(UNCHANGED_TEXT)}</span>')
    notes = [note for note in notes if note]
    separator = f' <span style="{UPDATED_STYLE}">·</span> '
    meta_html = "<br>" + separator.join(notes) if notes else ""

    li_style = LI_STYLE + (" " + UNCHANGED_STYLE if unchanged else "")
    return f'<li style="{li_style}">{pill} {name_html}{shared_html} — {comment_html}{meta_html}</li>'


def overview_table_html(tree, stats):
    keys = rag_keys_in_use(stats["rag_totals"])
    extra = extra_columns(stats)
    head = [f'<th style="{TH_STYLE}">Strategic initiative</th>', f'<th style="{TH_STYLE}">Items</th>']
    for key in keys:
        label = rag_style(key)["emoji"] + " " + esc(rag_display_label(key))
        head.append(f'<th style="{TH_STYLE}">{label}</th>')
    for header, _, _ in extra:
        head.append(f'<th style="{TH_STYLE}">{esc(header)}</th>')

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
        for _, per_initiative, _ in extra:
            value = per_initiative.get(initiative) or "–"
            cells.append(f'<td style="{TD_NUM_STYLE}">{value}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    total_cells = [
        f'<td style="{TD_STYLE}"><strong>Total</strong></td>',
        f'<td style="{TD_NUM_STYLE}"><strong>{stats["item_count"]}</strong></td>',
    ]
    for key in keys:
        total_cells.append(f'<td style="{TD_NUM_STYLE}"><strong>{stats["rag_totals"][key]}</strong></td>')
    for _, _, total in extra:
        total_cells.append(f'<td style="{TD_NUM_STYLE}"><strong>{total}</strong></td>')
    rows.append("<tr>" + "".join(total_cells) + "</tr>")

    return (f'<table style="{TABLE_STYLE}"><thead><tr>' + "".join(head) + "</tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


def footer_text(stats):
    notes = []
    if SORT_ITEMS_BY_RAG:
        notes.append("Within each product group, items are sorted by RAG status with the most urgent first.")
    if stats.get("tracking") and UNCHANGED_TO_BOTTOM:
        if stats.get("snapshot"):
            basis = "compared with the previous report" + (f" of {stats['compared_with']}" if stats.get("compared_with") else "")
        else:
            basis = f"not updated in Jira for {UNCHANGED_AFTER_DAYS} days or more"
        notes.append("Greyed-out items at the end of each group have no change to their delivery comment "
                     f"or RAG ({basis}).")
    if stats.get("has_dates"):
        notes.append(f"Dates in red mark items not updated for more than {STALE_AFTER_DAYS} days.")
    notes.append("Generated automatically from Jira.")
    return " ".join(notes)


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
        changes = changes_text(stats)
        if changes:
            parts.append(f'<p style="{CHANGES_STYLE}">{esc(changes)}</p>')
        if INCLUDE_OVERVIEW_TABLE:
            parts.append(overview_table_html(tree, stats))
    else:
        parts.append(f'<p style="{MUTED_STYLE}">No work items were supplied.</p>')

    parts.append(f'<hr style="{HR_STYLE}">')

    for initiative, groups in tree.items():
        meta = initiative_meta(initiative, stats)
        parts.append(f'<h2 style="{H2_STYLE}">{initiative_icon(initiative)} {esc(initiative)}</h2>')
        parts.append(f'<p style="{H2_META_STYLE}">{esc(meta)}</p>')
        for group, bucket in groups.items():
            count_html = f'<span style="{SMALL_MUTED_STYLE}">({len(bucket)})</span>'
            parts.append(f'<h3 style="{H3_STYLE}">{esc(group)} {count_html}</h3>')
            parts.append(f'<ul style="{LIST_STYLE}">')
            parts.extend(item_html(item, group) for item in bucket)
            parts.append("</ul>")

    parts.append(f'<hr style="{HR_STYLE}">')
    parts.append(f'<p style="{SMALL_MUTED_STYLE}">{esc(footer_text(stats))}</p>')
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
    tag = " 🆕 **NEW**" if item.get("change") == "new" else ""
    shared = ""
    others = [g for g in item["groups"] if g != current_group]
    if others:
        shared = " _(also under " + md_escape(", ".join(others)) + ")_"
    comment = item["comment"].replace("\n", " ") if item["comment"] else f"_{NO_COMMENT_TEXT}_"
    notes = []
    if item.get("updated"):
        text = updated_text(item)
        notes.append(f"**⚠ {text}**" if item.get("stale") else text)
    if item.get("change") == "unchanged":
        notes.append(UNCHANGED_TEXT)
    meta = " _(" + "; ".join(notes) + ")_" if notes else ""
    prefix = "⏸ " if item.get("change") == "unchanged" else ""
    return f"- {prefix}{style['emoji']} **{md_escape(item['rag_label'])}** · **{name}**{tag}{shared} — {comment}{meta}"


def overview_table_md(tree, stats):
    keys = rag_keys_in_use(stats["rag_totals"])
    extra = extra_columns(stats)
    header = ["Strategic initiative", "Items"] + [rag_style(k)["emoji"] + " " + rag_display_label(k) for k in keys]
    header += [name for name, _, _ in extra]
    lines = ["| " + " | ".join(header) + " |", "|:--|" + "--:|" * (len(header) - 1)]
    for initiative in tree:
        counter = stats["per_initiative"][initiative]
        cells = [initiative_icon(initiative) + " " + md_escape(initiative), str(sum(counter.values()))]
        cells += [str(counter.get(k) or "–") for k in keys]
        cells += [str(per_initiative.get(initiative) or "–") for _, per_initiative, _ in extra]
        lines.append("| " + " | ".join(cells) + " |")
    totals = ["**Total**", f"**{stats['item_count']}**"] + [f"**{stats['rag_totals'][k]}**" for k in keys]
    totals += [f"**{total}**" for _, _, total in extra]
    lines.append("| " + " | ".join(totals) + " |")
    return lines


def render_markdown(title, subtitle, tree, stats):
    lines = [f"# 📋 {md_escape(title)}", f"_{subtitle}_", ""]
    if stats["item_count"]:
        lines += ["**RAG roll-up:** " + rag_counts_text(stats["rag_totals"]), ""]
        changes = changes_text(stats)
        if changes:
            lines += [f"**Changes:** {changes}", ""]
        if INCLUDE_OVERVIEW_TABLE:
            lines += overview_table_md(tree, stats) + [""]
    else:
        lines += ["_No work items were supplied._", ""]
    lines.append("---")

    for initiative, groups in tree.items():
        meta = initiative_meta(initiative, stats)
        lines += ["", f"## {initiative_icon(initiative)} {md_escape(initiative)}", f"_{meta}_"]
        for group, bucket in groups.items():
            lines += ["", f"### {md_escape(group)} ({len(bucket)})", ""]
            lines.extend(item_md(item, group) for item in bucket)

    lines += ["", "---", f"_{footer_text(stats)}_"]
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
    # optional inputs: one entry per work item
    urls = parse_list(data.get("urls"), "urls", [])
    keys = parse_list(data.get("keys"), "keys", [])
    updated = parse_list(data.get("updated"), "updated", [])
    # optional inputs: report-wide
    previous = parse_previous(data.get("previous"), warnings)
    now = parse_timestamp(data.get("as_of")) or datetime.now(timezone.utc)
    link_items = str(data.get("link_items", "true")).strip().lower() not in ("0", "false", "no", "off")

    items = build_items(summaries, initiatives, groups, comments, rags, warnings, urls, keys, updated)
    if not items:
        warnings.append("No work items were found in the input data.")
    if not link_items:
        for item in items:
            item["url"] = ""
    mark_changes(items, previous, now)
    tree = group_items(items)
    stats = compute_stats(items, tree)
    stats["snapshot"] = previous is not None
    stats["compared_with"] = previous["label"] if previous else ""

    today = now.astimezone(display_timezone())
    date_text = f"{today.day} {today:%B %Y}"
    title = clean_text(data.get("title")).strip('"') or f"{REPORT_TITLE} – {date_text}"
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
        "changes_summary": changes_text(stats),
        "item_count": stats["item_count"],
        "initiative_count": stats["initiative_count"],
        "product_group_count": stats["product_group_count"],
        "new_count": stats["new_count"],
        "unchanged_count": stats["unchanged_count"],
        "stale_count": stats["stale_count"],
        "compared_with": stats["compared_with"],
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
    if output["changes_summary"]:
        print(output["changes_summary"])
    if output["warnings"]:
        print("Warnings: " + output["warnings"])
