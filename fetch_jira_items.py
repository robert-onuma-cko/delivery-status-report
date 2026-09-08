#!/usr/bin/env python3
"""
Pull the work items for the Delivery Status Report straight from Jira.

Produces the same five lists the Zapier step receives (summary,
strategic_initiatives, product_group, delivery_comment, delivery_rag) plus the
issue keys and browse URLs, so the report can be generated without Zapier.

Usage
    python fetch_jira_items.py                      # writes data/latest_input.json
    python fetch_jira_items.py --print              # print the JSON instead of writing it
    python fetch_jira_items.py --list-fields rag    # find custom field ids whose name contains "rag"
    python fetch_jira_items.py --jql "project = FIN AND sprint in openSprints()"

Secrets come from environment variables (never put them in code or config):
    JIRA_EMAIL        Atlassian account email
    JIRA_API_TOKEN    token from https://id.atlassian.com/manage-profile/security/api-tokens

Site URL, JQL and field ids live in report_config.json.
Only the Python standard library is used.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "report_config.json"
DEFAULT_OUTPUT = HERE / "data" / "latest_input.json"
OUTPUT_KEYS = ("summary", "strategic_initiatives", "product_group", "delivery_comment", "delivery_rag")

# Atlassian Document Format block nodes get a line break when flattened to text.
ADF_BLOCK_NODES = {
    "paragraph", "heading", "blockquote", "codeBlock", "panel", "rule", "listItem",
    "bulletList", "orderedList", "table", "tableRow", "mediaSingle", "expand",
}


class JiraError(RuntimeError):
    """Anything that stops us getting data out of Jira, with a readable hint."""


def load_config(path):
    path = Path(path)
    if not path.exists():
        raise JiraError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def credentials_from_env():
    email = os.environ.get("JIRA_EMAIL", "").strip()
    token = os.environ.get("JIRA_API_TOKEN", "").strip()
    if not email or not token:
        raise JiraError("Set the JIRA_EMAIL and JIRA_API_TOKEN environment variables (see .env.example).")
    return email, token


def validate_jira_config(jira_cfg):
    problems = []
    if "CHANGE_ME" in jira_cfg.get("jql", "CHANGE_ME"):
        problems.append("jira.jql")
    for name, field_id in (jira_cfg.get("fields") or {}).items():
        if not field_id or "CHANGE_ME" in field_id:
            problems.append(f"jira.fields.{name}")
    if problems:
        raise JiraError("report_config.json still has placeholders in: " + ", ".join(problems)
                        + ". Run `python fetch_jira_items.py --list-fields <name>` to look up field ids.")


class JiraClient:
    def __init__(self, base_url, email, token, timeout=60):
        self.base_url = base_url.rstrip("/")
        auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    def request(self, method, path, payload=None, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=body, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:600]
            hints = {
                400: "check jira.jql and the field ids in report_config.json",
                401: "check JIRA_EMAIL / JIRA_API_TOKEN",
                403: "the account is not allowed to see these issues",
                404: "check jira.base_url",
                429: "rate limited by Jira, try again later",
            }
            hint = hints.get(err.code, "")
            raise JiraError(f"Jira returned HTTP {err.code} for {method} {path}"
                            + (f" ({hint})" if hint else "") + f": {detail}") from None
        except urllib.error.URLError as err:
            raise JiraError(f"Could not reach {url}: {err.reason}. When this runs in a cloud sandbox, "
                            "make sure network access to the Jira host is allowed.") from None
        return json.loads(raw) if raw else {}

    def search(self, jql, fields, page_size=100):
        """Yield every issue matching the JQL, following Jira Cloud's token pagination."""
        payload = {"jql": jql, "fields": list(fields), "maxResults": page_size}
        while True:
            page = self.request("POST", "/rest/api/3/search/jql", payload)
            for issue in page.get("issues", []):
                yield issue
            token = page.get("nextPageToken")
            if not token or page.get("isLast"):
                return
            payload["nextPageToken"] = token

    def list_fields(self):
        return self.request("GET", "/rest/api/3/field")


# --- field value helpers -----------------------------------------------------
def adf_to_text(node):
    """Flatten an Atlassian Document Format tree (rich text field) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(child) for child in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type")
    attrs = node.get("attrs") or {}
    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    if node_type in ("mention", "emoji", "status"):
        return attrs.get("text") or attrs.get("shortName", "")
    if node_type == "inlineCard":
        return attrs.get("url", "")
    if node_type == "date":
        try:
            moment = datetime.fromtimestamp(int(attrs.get("timestamp", 0)) / 1000, tz=timezone.utc)
            return moment.strftime("%d %b %Y")
        except (TypeError, ValueError, OSError, OverflowError):
            return ""
    text = "".join(adf_to_text(child) for child in node.get("content", []))
    if node_type in ADF_BLOCK_NODES and not text.endswith("\n"):
        text += "\n"
    return text


def field_to_text(value):
    """One value as text: strings, select options, users, ADF documents, or lists joined with ', '."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        if value.get("type") == "doc" or "content" in value:
            return adf_to_text(value).strip() or None
        for key in ("value", "name", "displayName", "text", "key"):
            if value.get(key):
                return str(value[key])
        return None
    if isinstance(value, (list, tuple)):
        parts = [field_to_text(item) for item in value]
        parts = [part for part in parts if part]
        return ", ".join(parts) if parts else None
    return str(value)


def field_to_list(value):
    """A multi-select style value as a list of strings."""
    if isinstance(value, (list, tuple)):
        return [text for text in (field_to_text(item) for item in value) if text]
    text = field_to_text(value)
    return [text] if text else []


# --- main fetch ----------------------------------------------------------------
def fetch_items(config, email, token, jql_override=None):
    """Return the report input lists for every issue matching the configured JQL."""
    jira_cfg = dict(config["jira"])
    if jql_override:
        jira_cfg["jql"] = jql_override
    validate_jira_config(jira_cfg)

    fields = jira_cfg["fields"]
    client = JiraClient(jira_cfg["base_url"], email, token)
    wanted = list(dict.fromkeys(fields.values()))      # unique field ids, order preserved
    browse = jira_cfg["base_url"].rstrip("/") + "/browse/"

    result = {key: [] for key in OUTPUT_KEYS}
    result["keys"] = []
    result["urls"] = []
    for issue in client.search(jira_cfg["jql"], wanted, int(jira_cfg.get("page_size", 100))):
        values = issue.get("fields") or {}
        key = issue.get("key", "")
        result["keys"].append(key)
        result["urls"].append(browse + key if key else "")
        result["summary"].append(field_to_text(values.get(fields["summary"])) or key or "Untitled")
        result["strategic_initiatives"].append(field_to_text(values.get(fields["strategic_initiative"])))
        result["product_group"].append(field_to_list(values.get(fields["product_group"])))
        result["delivery_comment"].append(field_to_text(values.get(fields["delivery_comment"])))
        result["delivery_rag"].append(field_to_text(values.get(fields["delivery_rag"])))

    result["meta"] = {
        "source": "jira",
        "base_url": jira_cfg["base_url"],
        "jql": jira_cfg["jql"],
        "count": len(result["keys"]),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return result


def print_matching_fields(client, needle):
    needle = needle.lower()
    rows = []
    for field in client.list_fields():
        name = field.get("name", "")
        field_id = field.get("id", "")
        if needle in name.lower() or needle == field_id.lower():
            schema = field.get("schema") or {}
            kind = schema.get("type", "")
            if schema.get("items"):
                kind += f" of {schema['items']}"
            rows.append((name, field_id, kind))
    if not rows:
        print(f"No Jira fields contain '{needle}'.")
        return
    width = max(len(row[0]) for row in rows)
    print(f"{'Field name'.ljust(width)}  {'Field id':<22}  Type")
    for name, field_id, kind in sorted(rows):
        print(f"{name.ljust(width)}  {field_id:<22}  {kind}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch Delivery Status Report items from Jira.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to report_config.json")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="where to write the JSON")
    parser.add_argument("--print", dest="print_only", action="store_true", help="print instead of writing")
    parser.add_argument("--jql", help="override jira.jql from the config for this run")
    parser.add_argument("--list-fields", metavar="TEXT", help="list Jira fields whose name contains TEXT, then exit")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # non-ASCII safe on Windows consoles

    try:
        from local_env import load_dotenv
        load_dotenv(HERE / ".env")
    except ImportError:
        pass

    try:
        config = load_config(args.config)
        email, token = credentials_from_env()
        if args.list_fields is not None:
            print_matching_fields(JiraClient(config["jira"]["base_url"], email, token), args.list_fields)
            return 0
        data = fetch_items(config, email, token, args.jql)
    except JiraError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.print_only:
        print(text)
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"Fetched {data['meta']['count']} issues -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
