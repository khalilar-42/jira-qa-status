#!/usr/bin/env python3
import argparse
import ast
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests


JIRA_CORE_ENV_VARS = [
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
]
JIRA_FILTER_ENV_VAR = "JIRA_FILTER_ID"

SLACK_REQUIRED_ENV_VARS = [
    "SLACK_BOT_TOKEN",
    "SLACK_CHANNEL_ID",
]
SLACK_WEBHOOK_ENV_VAR = "SLACK_WEBHOOK_URL"

SEARCH_FIELDS = [
    "summary",
    "assignee",
    "priority",
    "updated",
    "status",
    "project",
]

PRIORITY_RANKS = {
    "blocker": 70,
    "critical": 60,
    "highest": 50,
    "high": 40,
    "medium": 30,
    "low": 20,
    "lowest": 10,
}


def get_issue_priority_name(issue: Dict) -> str:
    return (((issue.get("fields") or {}).get("priority") or {}).get("name") or "").strip()


def get_issue_priority_rank(issue: Dict) -> int:
    return PRIORITY_RANKS.get(get_issue_priority_name(issue).lower(), 0)


def load_dotenv_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue

            value = value.strip()
            if value and value[0] == value[-1] and value[0] in {"'", '"'}:
                try:
                    value = ast.literal_eval(value)
                except (SyntaxError, ValueError):
                    value = value[1:-1]

            os.environ[key] = value


def read_config(require_slack: bool, require_filter: bool) -> Dict[str, str]:
    cfg = {
        key: os.getenv(key, "").strip()
        for key in JIRA_CORE_ENV_VARS + [JIRA_FILTER_ENV_VAR, SLACK_WEBHOOK_ENV_VAR] + SLACK_REQUIRED_ENV_VARS
    }
    required = list(JIRA_CORE_ENV_VARS)
    if require_filter:
        required.append(JIRA_FILTER_ENV_VAR)
    missing = [k for k in required if not cfg.get(k)]
    if require_slack and not cfg.get(SLACK_WEBHOOK_ENV_VAR):
        missing.extend(k for k in SLACK_REQUIRED_ENV_VARS if not cfg.get(k))
    if missing:
        raise ValueError(
            "Missing environment variables: " + ", ".join(dict.fromkeys(missing))
        )

    cfg["JIRA_BASE_URL"] = cfg["JIRA_BASE_URL"].rstrip("/")
    cfg["JIRA_JQL"] = os.getenv("JIRA_JQL", "").strip()
    cfg["JIRA_AUTH_MODE"] = os.getenv("JIRA_AUTH_MODE", "site").strip().lower()
    cfg["JIRA_CLOUD_ID"] = os.getenv("JIRA_CLOUD_ID", "").strip()
    cfg["JIRA_SEARCH_MODE"] = os.getenv("JIRA_SEARCH_MODE", "auto").strip().lower()
    if cfg["JIRA_AUTH_MODE"] not in {"site", "scoped"}:
        raise ValueError("JIRA_AUTH_MODE must be either 'site' or 'scoped'.")
    if cfg["JIRA_SEARCH_MODE"] not in {"auto", "enhanced", "legacy"}:
        raise ValueError("JIRA_SEARCH_MODE must be one of: auto, enhanced, legacy.")
    return cfg


def resolve_cloud_id(cfg: Dict[str, str], timeout: int = 30) -> str:
    if cfg.get("JIRA_CLOUD_ID"):
        return cfg["JIRA_CLOUD_ID"]

    tenant_url = f"{cfg['JIRA_BASE_URL']}/_edge/tenant_info"
    resp = requests.get(tenant_url, headers={"Accept": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    cloud_id = data.get("cloudId") or data.get("id") or ""
    if not cloud_id:
        raise RuntimeError("Could not determine JIRA_CLOUD_ID from /_edge/tenant_info.")
    cfg["JIRA_CLOUD_ID"] = cloud_id
    return cloud_id


def resolve_jira_api_base(cfg: Dict[str, str], timeout: int = 30) -> str:
    if cfg["JIRA_AUTH_MODE"] == "site":
        return cfg["JIRA_BASE_URL"]

    cloud_id = resolve_cloud_id(cfg, timeout=timeout)
    return f"https://api.atlassian.com/ex/jira/{cloud_id}"


def jira_get(
    cfg: Dict[str, str],
    path: str,
    params: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict:
    return jira_request(cfg, "GET", path, params=params, timeout=timeout)


def jira_post(
    cfg: Dict[str, str],
    path: str,
    json_body: Optional[Dict] = None,
    timeout: int = 30,
) -> Dict:
    return jira_request(cfg, "POST", path, json_body=json_body, timeout=timeout)


def jira_request(
    cfg: Dict[str, str],
    method: str,
    path: str,
    params: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict] = None,
    timeout: int = 30,
) -> Dict:
    url = f"{cfg['JIRA_API_BASE']}{path}"
    headers = {"Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    resp = requests.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json_body,
        auth=(cfg["JIRA_EMAIL"], cfg["JIRA_API_TOKEN"]),
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def jira_issue_count(data: Dict) -> int:
    return len(data.get("issues") or [])


def print_search_debug(prefix: str, endpoint_label: str, data: Dict) -> None:
    print(
        f"{prefix} endpoint={endpoint_label} "
        f"response_keys={list(data.keys())} "
        f"total={data.get('total')} returned={jira_issue_count(data)}"
    )
    if data.get("errorMessages"):
        print(f"{prefix} errorMessages={data.get('errorMessages')}")
    if data.get("warningMessages"):
        print(f"{prefix} warningMessages={data.get('warningMessages')}")


def is_removed_search_api_error(err: requests.HTTPError) -> bool:
    if err.response is None:
        return False
    body = (err.response.text or "").lower()
    return "requested api has been removed" in body


def response_has_removed_search_api_error(data: Dict) -> bool:
    errors = [str(message).lower() for message in data.get("errorMessages") or []]
    return any("requested api has been removed" in message for message in errors)


def issue_search_request(
    cfg: Dict[str, str],
    endpoint_path: str,
    jql: str,
    fields: Optional[List[str]] = None,
    max_results: int = 100,
    timeout: int = 30,
) -> Dict:
    return jira_post(
        cfg,
        endpoint_path,
        json_body={
            "jql": jql,
            "maxResults": max_results,
            "fields": fields or SEARCH_FIELDS,
        },
        timeout=timeout,
    )


def run_issue_search(
    cfg: Dict[str, str],
    jql: str,
    fields: Optional[List[str]] = None,
    max_results: int = 100,
    timeout: int = 30,
    debug: bool = False,
) -> Dict[str, object]:
    search_mode = cfg["JIRA_SEARCH_MODE"]
    attempts = []
    search_plan = []
    if search_mode in {"auto", "enhanced"}:
        search_plan.append(("enhanced", "/rest/api/3/search/jql"))
    if search_mode in {"auto", "legacy"}:
        search_plan.append(("legacy", "/rest/api/3/search"))

    first_success = None
    last_error = None
    for endpoint_label, endpoint_path in search_plan:
        attempts.append(endpoint_label)
        try:
            data = issue_search_request(
                cfg,
                endpoint_path,
                jql,
                fields=fields,
                max_results=max_results,
                timeout=timeout,
            )
        except requests.HTTPError as err:
            last_error = err
            if search_mode == "auto":
                if debug:
                    body = err.response.text[:300] if err.response is not None else ""
                    print(f"[DEBUG] endpoint={endpoint_label} failed: {err} body={body}")
                if endpoint_label == "legacy" and is_removed_search_api_error(err):
                    continue
                continue
            raise

        if first_success is None:
            first_success = {
                "data": data,
                "endpoint": endpoint_label,
                "attempts": list(attempts),
            }
        if search_mode == "auto" and endpoint_label == "legacy" and response_has_removed_search_api_error(data):
            if debug:
                print("[DEBUG] endpoint=legacy returned an API removal message, ignoring fallback result.")
            continue
        if debug:
            print_search_debug("[DEBUG]", endpoint_label, data)

        if jira_issue_count(data) > 0 or endpoint_label == search_plan[-1][0]:
            return {
                "data": data,
                "endpoint": endpoint_label,
                "attempts": list(attempts),
            }

    if first_success is not None:
        return first_success
    if last_error is not None:
        raise last_error
    raise RuntimeError("Jira search did not return a usable response.")


def get_filter_jql(cfg: Dict[str, str], timeout: int = 30, debug: bool = False) -> str:
    filter_id = cfg["JIRA_FILTER_ID"]
    data = jira_get(cfg, f"/rest/api/3/filter/{filter_id}", timeout=timeout)
    jql = (data.get("jql") or "").strip()
    if not jql:
        raise RuntimeError(f"Filter {filter_id} returned no JQL.")

    if debug:
        print(f"[DEBUG] Filter ID: {filter_id}")
        print(f"[DEBUG] Filter name: {data.get('name')}")
        owner = data.get("owner") or {}
        print(f"[DEBUG] Filter owner: {owner.get('displayName')}")
        print(f"[DEBUG] Filter JQL: {jql}")
    return jql


def fetch_qa_issues(
    cfg: Dict[str, str],
    timeout: int = 30,
    debug: bool = False,
    debug_full: bool = False,
    jql_override: Optional[str] = None,
) -> List[Dict]:
    jql_source = "override"
    if jql_override:
        jql = jql_override
    elif cfg["JIRA_JQL"]:
        jql = cfg["JIRA_JQL"]
        jql_source = "env:JIRA_JQL"
    else:
        jql = get_filter_jql(cfg, timeout=timeout, debug=debug)
        jql_source = f"filter:{cfg['JIRA_FILTER_ID']}"
    search_result = run_issue_search(cfg, jql, timeout=timeout, debug=debug)
    data = search_result["data"]
    if debug:
        print(f"[DEBUG] JQL: {jql}")
        print(f"[DEBUG] JQL source: {jql_source}")
        print(f"[DEBUG] search_mode={cfg['JIRA_SEARCH_MODE']}")
        print(f"[DEBUG] search_attempts={','.join(search_result['attempts'])}")
        print(f"[DEBUG] search_endpoint={search_result['endpoint']}")
        if jira_issue_count(data) == 0 and search_result["endpoint"] == "enhanced":
            print(
                "[DEBUG] Enhanced search returned zero issues. "
                "If the Jira UI still shows tickets, the saved filter may be narrower "
                "than expected or Jira search indexing may still be catching up."
            )
    if debug_full:
        print("[DEBUG_FULL] ----- BEGIN RAW JIRA RESPONSE -----")
        print(json.dumps(data, indent=2, sort_keys=True))
        print("[DEBUG_FULL] ----- END RAW JIRA RESPONSE -----")
    return data.get("issues", [])


def diagnose_access(cfg: Dict[str, str], jql_override: Optional[str]) -> None:
    print(f"[DIAG] auth_mode={cfg.get('JIRA_AUTH_MODE')} api_base={cfg.get('JIRA_API_BASE')}")
    if cfg.get("JIRA_AUTH_MODE") == "scoped":
        print(f"[DIAG] cloud_id={cfg.get('JIRA_CLOUD_ID')}")

    try:
        me = jira_get(cfg, "/rest/api/3/myself")
        print("[DIAG] Authenticated Jira user:")
        print(
            f"[DIAG] displayName={me.get('displayName')} "
            f"email={me.get('emailAddress')} accountId={me.get('accountId')}"
        )
    except requests.HTTPError as err:
        status_code = err.response.status_code if err.response is not None else "?"
        print(
            f"[DIAG] Could not read /myself (status={status_code}). "
            "Continuing with search diagnostics."
        )
        print(
            "[DIAG] Note: /myself needs additional user-profile access. "
            "A token can fail here and still be valid for Jira issue search."
        )
        if err.response is not None and err.response.text:
            print(f"[DIAG] /myself response: {err.response.text[:300]}")

    if jql_override:
        configured_jql = jql_override
    elif cfg["JIRA_JQL"]:
        configured_jql = cfg["JIRA_JQL"]
    else:
        configured_jql = get_filter_jql(cfg, debug=True)

    checks = [
        ("Configured query", configured_jql),
        ("Broad sanity query", "updated >= -365d ORDER BY updated DESC"),
    ]

    for label, jql in checks:
        search_result = run_issue_search(
            cfg,
            jql,
            fields=["summary", "status", "project"],
            max_results=5,
            debug=True,
        )
        data = search_result["data"]
        issues = data.get("issues", [])
        print(f"[DIAG] {label}: {jql}")
        print(
            f"[DIAG] endpoint={search_result['endpoint']} "
            f"attempts={','.join(search_result['attempts'])}"
        )
        print_search_debug("[DIAG]", search_result["endpoint"], data)
        for issue in issues:
            fields = issue.get("fields", {})
            project_key = (fields.get("project") or {}).get("key", "N/A")
            status_name = (fields.get("status") or {}).get("name", "N/A")
            print(f"[DIAG] issue={issue.get('key')} project={project_key} status={status_name}")


def build_message(cfg: Dict[str, str], issues: List[Dict]) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"*Jira QA Check* ({now_utc})\n*Total QA tickets:* {len(issues)}"

    if not issues:
        return header + "\n_No QA tickets right now._"

    lines = [header]
    sorted_issues = sorted(
        issues,
        key=lambda issue: (
            get_issue_priority_rank(issue),
            (issue.get("fields") or {}).get("updated") or "",
            issue.get("key") or "",
        ),
        reverse=True,
    )

    grouped_issues: Dict[str, List[Dict]] = {}
    for issue in sorted_issues:
        priority_name = get_issue_priority_name(issue) or "Other"
        grouped_issues.setdefault(priority_name, []).append(issue)

    for priority_name, priority_issues in grouped_issues.items():
        lines.append(f"*{priority_name}*")
        for issue in priority_issues:
            key = issue.get("key", "?")
            fields = issue.get("fields", {})
            summary = (fields.get("summary") or "(no summary)").replace("\n", " ").strip()
            ticket_url = f"{cfg['JIRA_BASE_URL']}/browse/{key}"
            lines.append(f"• <{ticket_url}|{key}> | {summary}")

    return "\n".join(lines)


def post_to_slack(cfg: Dict[str, str], text: str, timeout: int = 30) -> None:
    webhook_url = cfg.get(SLACK_WEBHOOK_ENV_VAR, "")
    if webhook_url:
        resp = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json; charset=utf-8"},
            json={"text": text},
            timeout=timeout,
        )
        resp.raise_for_status()
        return

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {cfg['SLACK_BOT_TOKEN']}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "channel": cfg["SLACK_CHANNEL_ID"],
        "text": text,
        "unfurl_links": False,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Jira QA tickets from a saved filter and post them to Slack."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print Slack message to console without posting.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print JQL and raw Jira count diagnostics.",
    )
    parser.add_argument(
        "--debug-full",
        action="store_true",
        help="Print full raw Jira search JSON response.",
    )
    parser.add_argument(
        "--jql",
        help="Override JQL at runtime for troubleshooting.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Print Jira account + query diagnostics without posting to Slack.",
    )
    parser.add_argument(
        "--search-mode",
        choices=["auto", "enhanced", "legacy"],
        help="Override Jira search endpoint strategy for troubleshooting.",
    )
    args = parser.parse_args()

    try:
        load_dotenv_file()
        require_filter = not args.jql and not os.getenv("JIRA_JQL", "").strip()
        cfg = read_config(
            require_slack=not args.dry_run and not args.diagnose,
            require_filter=require_filter,
        )
        if args.search_mode:
            cfg["JIRA_SEARCH_MODE"] = args.search_mode
        cfg["JIRA_API_BASE"] = resolve_jira_api_base(cfg)

        if args.diagnose:
            diagnose_access(cfg, args.jql)
            return 0

        issues = fetch_qa_issues(
            cfg,
            debug=args.debug or args.debug_full,
            debug_full=args.debug_full,
            jql_override=args.jql,
        )
        message = build_message(cfg, issues)

        if args.dry_run:
            print(message)
        else:
            post_to_slack(cfg, message)
            print("Posted QA report to Slack successfully.")

        return 0
    except requests.HTTPError as err:
        body = ""
        extra_hint = ""
        if err.response is not None:
            body = err.response.text[:500]
            if err.response.status_code == 401:
                extra_hint = (
                    " Hint: if you use a scoped Atlassian token, set JIRA_AUTH_MODE=scoped "
                    "and ensure read:jira-work scope is granted."
                )
        print(f"HTTP error: {err}. Response: {body}{extra_hint}", file=sys.stderr)
        return 1
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
