#!/usr/bin/env python3
"""
One command that builds and distributes the weekly Delivery Status Report.

    python run_weekly_report.py --source jira --email --commit

Steps
  1. Load the work items: from Jira (--source jira), a JSON file
     (--source file --input PATH) or the bundled sample data (--source sample).
  2. Build the report with delivery_status_report.build_report().
  3. Write reports/<YYYY-MM-DD>/delivery-status-report.{html,md}, summary.json
     and input.json, plus reports/latest.{html,md} and reports/latest_input.json.
     The next run compares its items with latest_input.json to grey out the
     ones whose delivery comment and RAG did not change.
     Also refresh the static site: index.html (this report) and archive.html
     (every dated report) at the repository root, and site.zip with the same
     files for a manual upload to the static hosting platform.
  4. --email   Send the report over SMTP (settings from environment variables).
  5. --commit  Commit the report and site files and push them to the configured
     branch (main by default, where the hosting integration deploys from).

The last line printed is always "STATUS: OK" or "STATUS: FAILED (reasons)".
Exit code 0 means every requested step succeeded.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from local_env import load_dotenv                  # noqa: E402
from delivery_status_report import build_report, UNCHANGED_AFTER_DAYS    # noqa: E402
import fetch_jira_items                            # noqa: E402
import send_report_email                           # noqa: E402

INPUT_KEYS = ("summary", "strategic_initiatives", "product_group", "delivery_comment", "delivery_rag")
DEFAULT_BRANCH = "main"
REPORT_FILE = "delivery-status-report.html"
SITE_FONT = "font-family: Arial, Helvetica, sans-serif;"


def log(message):
    print(message, flush=True)


def display_path(path):
    try:
        return str(Path(path).resolve().relative_to(HERE))
    except ValueError:
        return str(path)


def load_config(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def load_items(args, config):
    """Return (data, description) where data has the five input lists (+ optional keys/urls)."""
    if args.source == "jira":
        email, token = fetch_jira_items.credentials_from_env()
        data = fetch_jira_items.fetch_items(config, email, token, args.jql)
        return data, f"Jira ({data['meta']['jql']})"
    if args.input:
        path = Path(args.input)
    elif args.source == "sample":
        path = HERE / "sample_input.json"
    else:
        path = fetch_jira_items.DEFAULT_OUTPUT
    with path.open(encoding="utf-8") as handle:
        return json.load(handle), f"file {path}"


# --- static site ---------------------------------------------------------------
def escape_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def archive_entries(out_root):
    """Every dated report under out_root (reports/<YYYY-MM-DD>/), newest first, with its summary."""
    entries = []
    for folder in sorted(Path(out_root).glob("????-??-??"), reverse=True):
        if not (folder / REPORT_FILE).exists():
            continue
        summary = {}
        summary_path = folder / "summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except ValueError:
                summary = {}
        entries.append({
            "date": folder.name,
            "href": f"{Path(out_root).name}/{folder.name}/{REPORT_FILE}",
            "title": summary.get("title") or f"Delivery Status Report – {folder.name}",
            "item_count": summary.get("item_count", ""),
            "rag_summary": summary.get("rag_summary", ""),
            "changes_summary": summary.get("changes_summary", ""),
        })
    return entries


def site_nav_html(archive_href, latest_href=None):
    """One-line navigation bar shown above the report / archive."""
    links = [f'<a href="{archive_href}" style="color: #2B6CB0;">📚 All reports</a>']
    if latest_href:
        links.insert(0, f'<a href="{latest_href}" style="color: #2B6CB0;">📋 Latest report</a>')
    return (f'<p style="{SITE_FONT} font-size: 13px; color: #718096; margin: 0 0 18px 0;">'
            + " &nbsp;·&nbsp; ".join(links) + "</p>")


def render_archive_html(entries):
    cell = 'style="border: 1px solid #E2E8F0; padding: 6px 10px; vertical-align: top;"'
    centred = 'style="border: 1px solid #E2E8F0; padding: 6px 10px; vertical-align: top; text-align: center;"'
    head = 'style="border: 1px solid #E2E8F0; padding: 6px 10px; background-color: #F7FAFC; text-align: left;"'
    rows = []
    for entry in entries:
        rows.append(
            "<tr>"
            f'<td {cell}><a href="{entry["href"]}" style="color: #2B6CB0; font-weight: bold; text-decoration: none;">'
            f'{escape_html(entry["title"])}</a></td>'
            f'<td {centred}>{escape_html(entry["item_count"])}</td>'
            f'<td {cell}>{escape_html(entry["rag_summary"])}</td>'
            f'<td {cell}>{escape_html(entry["changes_summary"])}</td>'
            "</tr>"
        )
    if not rows:
        rows.append(f'<tr><td {cell} colspan="4" style="color: #718096;">No reports yet.</td></tr>')
    fragment = (
        f'<div style="{SITE_FONT} color: #2D3748; line-height: 1.6;">\n'
        + site_nav_html("archive.html", latest_href="index.html") + "\n"
        f'<h1 style="{SITE_FONT} color: #1A202C; font-size: 26px; margin-bottom: 4px;">📚 Delivery Status Reports</h1>\n'
        f'<p style="{SITE_FONT} color: #718096; font-size: 13px; margin-top: 0; margin-bottom: 14px;">'
        f"{len(entries)} report{'s' if len(entries) != 1 else ''}, newest first</p>\n"
        '<table style="border-collapse: collapse; font-size: 13px;"><thead><tr>'
        f'<th {head}>Report</th><th {head}>Items</th><th {head}>RAG</th><th {head}>Changes</th>'
        "</tr></thead><tbody>\n" + "\n".join(rows) + "\n</tbody></table>\n</div>"
    )
    return send_report_email.wrap_html_document("Delivery Status Reports", fragment)


def write_site(site_root, out_root, title, report_html):
    """Write index.html + archive.html into site_root and zip them with every dated report."""
    site_root = Path(site_root)
    index_path = site_root / "index.html"
    archive_path = site_root / "archive.html"
    zip_path = site_root / "site.zip"
    entries = archive_entries(out_root)

    index_path.write_text(send_report_email.wrap_html_document(title, site_nav_html("archive.html") + "\n" + report_html),
                          encoding="utf-8")
    archive_path.write_text(render_archive_html(entries), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(index_path, "index.html")
        bundle.write(archive_path, "archive.html")
        for entry in entries:
            bundle.write(site_root / entry["href"], entry["href"])
    return [index_path, archive_path], zip_path


# --- git helpers ---------------------------------------------------------------
def run_git(args, cwd, check=True):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result


def ensure_git_identity(repo):
    defaults = (("user.email", "delivery-report-bot@users.noreply.github.com"),
                ("user.name", "Delivery Report Bot"))
    for key, fallback in defaults:
        if not run_git(["config", key], repo, check=False).stdout.strip():
            run_git(["config", key, fallback], repo)


def prepare_branch(repo, branch):
    """Check out `branch`; base it on the remote branch when it exists so history accumulates."""
    run_git(["rev-parse", "--is-inside-work-tree"], repo)
    current = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    if current == branch:
        return f"already on branch {branch}"
    fetched = run_git(["fetch", "origin", branch], repo, check=False)
    if fetched.returncode == 0:
        run_git(["checkout", "-B", branch, "FETCH_HEAD"], repo)
        return f"checked out {branch} from origin/{branch}"
    run_git(["checkout", "-B", branch], repo)
    return f"created branch {branch} from {current}"


def commit_and_push(repo, branch, paths, message):
    ensure_git_identity(repo)
    relative = [str(Path(path).resolve().relative_to(repo)) for path in paths]
    run_git(["add", "--"] + relative, repo)
    if not run_git(["status", "--porcelain", "--"] + relative, repo).stdout.strip():
        return "nothing new to commit"
    run_git(["commit", "-m", message], repo)
    run_git(["push", "-u", "origin", f"HEAD:{branch}"], repo)
    return f"committed and pushed to origin/{branch}"


# --- main ----------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(description="Build and distribute the weekly Delivery Status Report.")
    parser.add_argument("--source", choices=("jira", "file", "sample"), default="jira",
                        help="where the work items come from (default: jira)")
    parser.add_argument("--input", help="JSON file to read for --source file (default: data/latest_input.json)")
    parser.add_argument("--jql", help="override jira.jql from report_config.json for this run")
    parser.add_argument("--config", default=str(HERE / "report_config.json"))
    parser.add_argument("--out", default=str(HERE / "reports"),
                        help="reports directory (default: reports/); index.html, archive.html and site.zip go next to it")
    parser.add_argument("--title", help="override the document title")
    parser.add_argument("--previous", help="previous report's input.json used to detect unchanged items "
                                           "(default: <out>/latest_input.json)")
    parser.add_argument("--no-previous", action="store_true", help="do not compare with the previous report")
    parser.add_argument("--email", action="store_true", help="email the report over SMTP")
    parser.add_argument("--commit", action="store_true", help="commit the report files and push them")
    parser.add_argument("--branch", help="git branch for --commit (default: REPORT_GIT_BRANCH, then git.branch in config)")
    return parser.parse_args(argv)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # emoji-safe on Windows consoles
    args = parse_args(argv)
    load_dotenv(HERE / ".env")
    config = load_config(args.config)
    failures = []
    repo = HERE
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d")

    # 1. data ------------------------------------------------------------------
    log(f"[1/5] Loading work items ({args.source}) ...")
    try:
        data, source_desc = load_items(args, config)
    except Exception as err:  # noqa: BLE001 - any failure here means there is nothing to report
        log(f"ERROR: {err}")
        log("STATUS: FAILED (fetch)")
        return 1
    count = len(data.get("summary") or [])
    log(f"      {count} items from {source_desc}")
    if count == 0:
        log("ERROR: no work items were returned, so there is nothing to report (check jira.jql).")
        log("STATUS: FAILED (no items)")
        return 1

    # 2. branch first (so last week's files are in the working tree), then build --
    branch = args.branch or os.environ.get("REPORT_GIT_BRANCH") or (config.get("git") or {}).get("branch") or DEFAULT_BRANCH
    branch_ready = False
    if args.commit:
        try:
            log("      " + prepare_branch(repo, branch))
            branch_ready = True
        except Exception as err:  # noqa: BLE001
            failures.append(f"git branch: {err}")
            log(f"ERROR: could not prepare branch {branch}: {err}")
    out_root = Path(args.out)

    log("[2/5] Building the report ...")
    payload = {key: data.get(key) for key in INPUT_KEYS}
    for key in ("keys", "urls", "updated", "as_of"):
        if data.get(key):
            payload[key] = data[key]
    report_cfg = config.get("report") or {}
    payload["link_items"] = report_cfg.get("link_items_to_jira", True)
    title = args.title or report_cfg.get("title") or ""
    if title:
        payload["title"] = title

    fallback = f"items not updated for {UNCHANGED_AFTER_DAYS}+ days count as unchanged"
    previous_path = Path(args.previous) if args.previous else out_root / "latest_input.json"
    if args.no_previous or (args.source == "sample" and not args.previous):
        log(f"      change detection: no comparison with a previous report ({fallback})")
    elif previous_path.exists():
        try:
            payload["previous"] = json.loads(previous_path.read_text(encoding="utf-8"))
            previous_date = (payload["previous"].get("meta") or {}).get("report_date") or "date unknown"
            log(f"      change detection: comparing with {display_path(previous_path)} ({previous_date})")
        except (ValueError, AttributeError) as err:
            payload.pop("previous", None)
            log(f"      WARNING: could not read {display_path(previous_path)} ({err}); {fallback}")
    else:
        log(f"      change detection: {display_path(previous_path)} not found; {fallback}")

    output = build_report(payload)
    log(f"      {output['title']}: {output['item_count']} items, {output['initiative_count']} initiatives, "
        f"{output['product_group_count']} product groups")
    log(f"      RAG: {output['rag_summary']}")
    if output.get("changes_summary"):
        log(f"      Changes: {output['changes_summary']}")
    if output["warnings"]:
        log(f"      Warnings: {output['warnings']}")

    # 3. files -----------------------------------------------------------------
    log("[3/5] Writing report files ...")
    out_dir = out_root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    html_document = send_report_email.wrap_html_document(output["title"], output["report_html"])

    html_path = out_dir / "delivery-status-report.html"
    md_path = out_dir / "delivery-status-report.md"
    summary_path = out_dir / "summary.json"
    input_path = out_dir / "input.json"
    latest_html = out_root / "latest.html"
    latest_md = out_root / "latest.md"
    latest_input = out_root / "latest_input.json"

    html_path.write_text(html_document, encoding="utf-8")
    md_path.write_text(output["report_markdown"], encoding="utf-8")
    summary = {
        "title": output["title"],
        "generated_at": now.isoformat(timespec="seconds"),
        "source": source_desc,
        "item_count": output["item_count"],
        "initiative_count": output["initiative_count"],
        "product_group_count": output["product_group_count"],
        "rag_summary": output["rag_summary"],
        "changes_summary": output.get("changes_summary", ""),
        "new_count": output.get("new_count", 0),
        "unchanged_count": output.get("unchanged_count", 0),
        "stale_count": output.get("stale_count", 0),
        "compared_with": output.get("compared_with", ""),
        "warnings": output["warnings"],
    }
    if isinstance(data, dict):
        data.setdefault("meta", {})["report_date"] = stamp        # lets next week's run name this report
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    input_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copyfile(html_path, latest_html)
    shutil.copyfile(md_path, latest_md)
    shutil.copyfile(input_path, latest_input)
    written = [html_path, md_path, summary_path, input_path, latest_html, latest_md, latest_input]
    # static site: index.html / archive.html next to the reports folder, plus a zip for manual uploads
    site_files, zip_path = write_site(out_root.parent, out_root, output["title"], output["report_html"])
    written += site_files
    for path in written:
        log(f"      wrote {display_path(path)}")
    log(f"      wrote {display_path(zip_path)} (not committed; upload it by hand if the hosting integration is not wired up)")

    # 4. email -----------------------------------------------------------------
    if args.email:
        log("[4/5] Emailing the report ...")
        email_cfg = config.get("email") or {}
        settings = send_report_email.settings_from_env(email_cfg)
        missing = send_report_email.missing_settings(settings)
        if missing:
            failures.append("email not configured: set " + ", ".join(missing))
            log("ERROR: " + failures[-1])
        else:
            subject = (email_cfg.get("subject") or "{title}").format(title=output["title"], date=f"{now.day} {now:%B %Y}")
            try:
                recipients = send_report_email.send_report(subject, html_document, output["report_markdown"],
                                                           settings, attachments=[html_path, md_path])
                log(f"      sent '{subject}' to {', '.join(recipients)}")
            except Exception as err:  # noqa: BLE001
                failures.append(f"email: {err}")
                log(f"ERROR: email failed: {err}")
    else:
        log("[4/5] Email skipped (run with --email to send it)")

    # 5. commit ----------------------------------------------------------------
    if args.commit and branch_ready:
        log("[5/5] Committing report files ...")
        try:
            log("      " + commit_and_push(repo, branch, written, f"Weekly delivery status report {stamp}"))
        except Exception as err:  # noqa: BLE001
            failures.append(f"git: {err}")
            log(f"ERROR: git failed: {err}")
    elif args.commit:
        log("[5/5] Commit skipped because the branch could not be prepared")
    else:
        log("[5/5] Commit skipped (run with --commit to push the report to git)")

    if failures:
        log("STATUS: FAILED (" + "; ".join(failures) + ")")
        return 1
    log("STATUS: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
