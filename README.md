# jira-qa-status

## 1) Install dependency
```bash
cd "/Users/khalilar/Desktop/scripts/jira-qa-tickets script"
python3 -m pip install -r requirements.txt
```

## 2) Create a `.env` file
Copy `.env.example` to `.env` and fill in your real values. The script now auto-loads `.env` from the project folder, so you do not need to export everything in your shell.

```bash
cp .env.example .env
```

Example `.env` values:

```bash
JIRA_BASE_URL="https://your-domain.atlassian.net"
JIRA_EMAIL="you@company.com"
JIRA_API_TOKEN="..."
JIRA_FILTER_ID="12345"
JIRA_AUTH_MODE="scoped"
# Optional in scoped mode; leave empty to auto-detect
JIRA_CLOUD_ID=""
JIRA_SEARCH_MODE="auto"
# Option A: post with a bot token
SLACK_BOT_TOKEN="xoxb-..."
SLACK_CHANNEL_ID="C0123456789"
# Option B: post with an incoming webhook instead
SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

`JIRA_SEARCH_MODE` options:
- `auto`: try enhanced Jira search first, then fall back to legacy search if enhanced returns nothing
- `enhanced`: only use `/rest/api/3/search/jql`
- `legacy`: only use `/rest/api/3/search`

## 3) Test without posting to Slack
```bash
python3 jira_qa_to_slack.py --dry-run
```

## Troubleshooting auth / visibility
```bash
python3 jira_qa_to_slack.py --diagnose
python3 jira_qa_to_slack.py --diagnose --jql 'status = QA ORDER BY updated DESC'
python3 jira_qa_to_slack.py --dry-run --debug-full --jql 'status = QA ORDER BY updated DESC'
python3 jira_qa_to_slack.py --dry-run --search-mode legacy
```

## 4) Real run (posts in Slack)
```bash
python3 jira_qa_to_slack.py
```

## 5) Run autonomously with GitHub Actions
This repo includes a workflow at `.github/workflows/jira-qa-report.yml` that runs:
- manually from the GitHub `Actions` tab
- automatically Monday-Friday at `09:00` and `15:00` in the `Africa/Casablanca` timezone

Set these repository secrets in GitHub under `Settings > Secrets and variables > Actions`:
- `JIRA_BASE_URL`
- `JIRA_EMAIL`
- `JIRA_API_TOKEN`
- `JIRA_FILTER_ID`
- `JIRA_AUTH_MODE`
- `JIRA_SEARCH_MODE`
- `JIRA_CLOUD_ID` if you use scoped Atlassian tokens and want to set it explicitly
- `JIRA_JQL` only if you intentionally want to override the saved Jira filter
- `SLACK_WEBHOOK_URL` if you post through an incoming webhook
- `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID` if you post through `chat.postMessage` instead

Notes:
- Scheduled GitHub Actions run on the default branch.
- If `JIRA_JQL` is set as a secret, it overrides `JIRA_FILTER_ID`.
- If `SLACK_WEBHOOK_URL` is set, the script ignores `SLACK_CHANNEL_ID` and posts to the webhook's channel.

## 6) Secret handling
For GitHub Actions:
- Put production secrets only in `Settings > Secrets and variables > Actions`
- Do not commit `.env`
- After GitHub Secrets are configured, you can delete your local `.env` if you no longer need local runs

For local manual runs:
- `.env` is convenient but it is still plain text on disk
- If you do not want secrets stored in `.env`, use temporary shell variables for the current terminal session only
- If your current `.env` contains real Jira or Slack secrets, rotate them and replace the file contents with placeholders or delete the file

## 7) Local cron alternative
Open crontab:
```bash
crontab -e
```
Add:
```cron
0 9,15 * * 1-5 /usr/bin/env bash -lc 'cd "/Users/khalilar/Desktop/scripts/jira-qa-tickets script" && python3 jira_qa_to_slack.py >> "/Users/khalilar/Desktop/scripts/jira-qa-tickets script/jira_qa_bot.log" 2>&1'
```

This runs Monday-Friday. Remove `1-5` if you want every day.

## Slack app minimum setup
- Bot-token mode:
- Bot token with scope: `chat:write`
- Install app to workspace
- Invite bot to target channel
- Incoming-webhook mode:
- Enable Incoming Webhooks for the app and use `SLACK_WEBHOOK_URL`

## Jira minimum setup
- Jira Cloud account with access to the filter/issues
- API token from Atlassian account settings
