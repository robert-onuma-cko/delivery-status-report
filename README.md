# Delivery Status Report

Turns a list of work items (name, strategic initiative, product group, delivery comment, delivery RAG)
into a styled status report grouped by **strategic initiative → product group**, then distributes it.

Two ways to run it:

| Path | What runs | Output |
|---|---|---|
| **Zapier** | `delivery_status_report.py` pasted into a *Code by Zapier (Python)* step | `report_html` mapped into a Google Doc |
| **Weekly cloud routine** | `python run_weekly_report.py --source jira --email --commit` on a Claude Code routine (Haiku) | Email + `reports/` folder on the `claude/weekly-reports` branch |

Everything is standard-library Python 3.8+; nothing to install.

## Files

| File | Purpose |
|---|---|
| `delivery_status_report.py` | The report generator. Zapier step and importable module (`build_report`). |
| `run_weekly_report.py` | One command: fetch → build → write files → email → commit/push. Prints `STATUS: OK` or `STATUS: FAILED (...)` last. |
| `fetch_jira_items.py` | Pulls the work items from Jira via REST (JQL + custom fields). Also `--list-fields` to look up field ids. |
| `send_report_email.py` | Sends the report over SMTP (HTML body, Markdown text alternative, both attached). |
| `local_env.py` | Loads `.env` for local runs. |
| `report_config.json` | Non-secret settings: Jira site, JQL, field ids, recipients, branch. |
| `.env.example` | The secrets the scripts read from the environment. Copy to `.env` locally. |
| `ROUTINE_PROMPT.md` | The exact prompt/model/schedule of the cloud routine. |
| `test_delivery_status_report.py`, `sample_input.json`, `sample_output.*` | Local test harness and sample data/output. |

## Try it locally

```bash
python run_weekly_report.py --source sample
```

That writes `reports/<today>/delivery-status-report.html` (open it in a browser), the Markdown twin,
`summary.json`, `input.json`, and refreshes `reports/latest.*` plus `reports/latest_input.json` (the
snapshot the next run compares against; sample runs skip that comparison). Add `--email` once `.env` has
SMTP settings, and `--commit` to push the files to git.

Run the Zapier-path tests with `python test_delivery_status_report.py`.

## Configure Jira

1. Create an API token at <https://id.atlassian.com/manage-profile/security/api-tokens> and put it in
   `.env` as `JIRA_API_TOKEN` (with `JIRA_EMAIL`). Never commit it.
2. Find the custom field ids for *Strategic Initiative* and *Product Group*:

   ```bash
   python fetch_jira_items.py --list-fields initiative
   python fetch_jira_items.py --list-fields "product group"
   ```

   Delivery comment and RAG are pre-filled from the existing Zapier script (`customfield_15976`,
   `customfield_15930`); check them the same way.
3. Put the ids and your JQL into `report_config.json` (replace every `CHANGE_ME`), then test:

   ```bash
   python fetch_jira_items.py --print
   ```

Field handling: select lists → their value, multi-selects → a list (an item with two product groups
is listed under both), rich-text comments → plain text, empty → "No update provided" / "No RAG".
Jira's built-in `updated` timestamp is fetched as well (`jira.fields.updated`) and drives the
"Last updated" line. Item names link to `https://checkout.atlassian.net/browse/KEY` – built from the
issue key, because the REST API only returns machine `self` links – unless `report.link_items_to_jira`
is `false`.

## How items are ordered and highlighted

**Sections.** Strategic initiatives, and the product groups inside each one, are ordered by urgency
score: Off track counts 2, At risk / Spillover counts 1 (`URGENT_RAG_WEIGHTS`). Ties go to the section
with more items, then to the configured `INITIATIVE_ORDER` / alphabetical order. Each initiative shows
its score ("🔥 urgency score 5"). Set `SORT_SECTIONS_BY_URGENCY = False` to fall back to the fixed order.

**Within each product group:**

- **Most urgent first** – items are sorted by RAG: Off track, At risk / Spillover, On track, Planned,
  No RAG, Done, Dropped.
- **Unchanged items last, greyed out** – an item is unchanged when its delivery comment and RAG are
  identical to the previous report. The runner keeps last week's items in `reports/latest_input.json`
  on the report branch and compares against it; items that were not in the previous report get a
  **NEW** tag. Without a snapshot (first run, or the Zapier path) an item counts as unchanged when
  Jira's `updated` stamp is 7 or more days old.
- **Unchanged but At risk / Off track** – these are *not* greyed out or moved down. They keep their
  urgent position and carry a red "⚠ No change since the last report" note; the header counts them
  ("⏸ 12 unchanged (3 at risk or off track)").
- **Last updated** – every item shows "Last updated 3 days ago (6 Sep 2026)". It turns red once the
  item has not been updated for more than 14 days, except for Done and Dropped items
  (`STALE_EXEMPT_RAGS`), which are closed and never flagged.

The header sums it up ("Since the previous report (2 Sep 2026): 🆕 3 new · ✏️ 20 updated · ⏸ 12 unchanged
(3 at risk or off track) · ⚠ 6 not updated for 14+ days") and the overview table gains "No change" and
"14+ days" columns. The thresholds are the `STALE_AFTER_DAYS` and `UNCHANGED_AFTER_DAYS` constants at
the top of `delivery_status_report.py`; `--no-previous` skips the snapshot comparison for one run.

## Configure email

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` and `REPORT_EMAIL_TO`
(comma-separated). Recipients can also live in `report_config.json` under `email.to` / `email.cc`;
the environment variable wins when both are set. For Google Workspace use `smtp.gmail.com:587` with an
app password. Test without sending:

```bash
python send_report_email.py --html reports/latest.html --markdown reports/latest.md --dry-run
```

## The weekly cloud routine

The routine is a scheduled Claude Code cloud agent (see `ROUTINE_PROMPT.md`). It clones this repo into a
sandbox, runs the one command above, and reports the `STATUS` line. For it to work:

1. **Repository access** – the Claude GitHub App must be allowed to see this repo: open
   <https://github.com/settings/installations>, choose *Claude* → *Configure* → *Repository access* and add
   `delivery-status-report` (or pick the repo from the repository list at <https://claude.ai/code>). Until then,
   creating the routine fails with "You don't have access to a repository this routine uses".
2. **Environment variables** – add every variable from `.env.example` to the Claude Code environment the
   routine uses (claude.ai/code → Environments). Secrets never go in the repo.
3. **Network access** – the environment must be allowed to reach `checkout.atlassian.net` and your SMTP
   host. If the run fails with "Could not reach", widen the environment's network settings.
4. **Branch** – reports are committed to `claude/weekly-reports` (branches prefixed `claude/` are the ones
   cloud sessions may push to). Change it via `git.branch` in `report_config.json` or `REPORT_GIT_BRANCH`.
   Each week adds `reports/<date>/` and refreshes `reports/latest.*` and `reports/latest_input.json`
   (the snapshot the next run compares against), so the branch is the archive.
5. Manage or run the routine at <https://claude.ai/code/routines>.

## Zapier step (unchanged)

Paste `delivery_status_report.py` into a *Code by Zapier → Run Python* step. Map the inputs
`summary`, `strategic_initiatives`, `product_group`, `delivery_comment`, `delivery_rag` (each a JSON
array string; Python-style lists and comma-joined line items also work). Optional inputs: `title`,
`keys` (issue keys – item names become links), `urls` (REST `self` links and `undefined` values are
replaced by the `/browse/KEY` link), `updated` (Jira "updated" stamps – the "Last updated" line, red after
14 days, greyed out after 7 days without a change), `previous` (last week's lists as JSON, e.g. from
Storage by Zapier, for exact change detection) and `as_of`.
Outputs: `title` → Google Docs *Document Name*, `report_html` → *Document Content*, plus
`report_markdown`, `rag_summary`, `changes_summary`, counts (`item_count`, `new_count`,
`unchanged_count`, `stale_count`, ...) and `warnings`.

## Troubleshooting

| Message | Fix |
|---|---|
| `report_config.json still has placeholders` | Replace the `CHANGE_ME` values (JQL, field ids). |
| `Jira returned HTTP 401` | Wrong `JIRA_EMAIL` / `JIRA_API_TOKEN`, or the token was rotated. |
| `Jira returned HTTP 400` | Bad JQL or a field id that does not exist. |
| `Could not reach https://...` | Network access from the routine's environment is blocked. |
| `email not configured: set ...` | Add the listed environment variables. |
| `SMTP login failed` | Wrong password / app password, or SMTP AUTH disabled for the mailbox. |
| `git push ... failed` | The sandbox may only push to `claude/*` branches; check the branch name and repo permissions. |
| `STATUS: FAILED (no items)` | The JQL returned nothing this week. |
| Everything is greyed out | Nothing changed since the snapshot, or the same input was run twice. Run with `--no-previous` to compare by last-updated date instead. |
| `'updated' values could not be read as a date` | `jira.fields.updated` does not point at a date field; use Jira's built-in `updated`. |
