# Github Failed Actions MCP Companion

Python script for the official Github MCP: retrieve latest failed action runs / or through ID, analyze issues and fix them.

---

You can create a custom rule for your AI powered IDE (e.g. Cursor), that will use the [official Github MCP](https://github.com/github/github-mcp-server) to retrieve the latest failes action runs, download the artifacts and logs to a folder, analyze them and fix your CI tests.

In case the Github MCP is giving you auth issues, replace the MCP with this in your `mcp.json` instead:

```json
"github": {
    "command": "npx",
    "args": [
        "-y",
        "@modelcontextprotocol/server-github"
    ],
    "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "your_github_pat_here"
    }
}
```

You can create a new Personal Access Token here [https://github.com/settings/tokens](https://github.com/settings/tokens).

### Setup

Import this repo inside your workspace/project:

```bash
git clone https://github.com/Jany-M/github-failed-actions-mcp-companion
```

Create an .env file based on .env.example:

```bash
cp .env.example .env
```

Then edit `.env` and fill in your values:

```bash
GITHUB_TOKEN=your_github_pat_here
OWNER=Your_Github_Username
REPO=your-repo-name
LOG_DIR=_dev/github_action_logs
```

**Note**: The script uses a built-in `.env` file parser (no external dependencies required). It will automatically load configuration from the `.env` file, or you can use environment variables directly.

Create a new rule for your IDE, in your workspace/project, e.g. `.cursor/rules` (or whatever the folder for rules for your IDE is), paste this prompt as rule:


```txt
---
alwaysApply: true
---
## GitHub Repository Information

**Auto-detect from workspace**:
   - **Repository**: Extract from `git remote get-url origin` (format: `owner/repo`)
   - **Current Branch**: Extract from `git rev-parse --abbrev-ref HEAD`
   - **Default Owner**: Ask the user if not detected from git remote
   - **Default Repo**: Ask the user if not detected from git remote

**When checking GitHub Actions**:
   1. **First**: Get current workspace branch using `git rev-parse --abbrev-ref HEAD`
   2. **Second**: Get repo owner/name from `git remote get-url origin` (parse to get `owner/repo`)
   3. **Third**: Use these values when calling `fetch_github_actions_logs.py --branch <detected-branch>`
   4. **Fallback**: If branch detection fails, use `--all-failed` to check latest failed runs

## GitHub Actions Workflow Debugging

When debugging GitHub Actions workflow failures or checking workflow status:

**Use the dedicated script at `fetch_github_actions_logs.py`** - MCP tools do not reliably provide Actions workflow runs or logs. The custom script is the primary and recommended method for accessing GitHub Actions logs.

**Script usage** (with auto-detection):
   - **Always detect branch first**: Run `git rev-parse --abbrev-ref HEAD` to get current workspace branch
   - **Always detect repo**: Run `git remote get-url origin` to get repo owner/name
   - **Then run**: `python fetch_github_actions_logs.py --branch <detected-branch> [--run-id RUN_ID] [--all-failed]`
   - The script will:
     - **Clean up old logs** in `github_action_logs/` at the start of each run (use `--keep-old-logs` to preserve them)
     - List recent workflow runs for the detected branch (or latest failed if branch unavailable)
     - Show failed runs and jobs
     - Attempt to fetch and save logs for failed jobs to `github_action_logs/`
     - Replace existing log files if the same job is fetched again
     - Display the last 50 lines of logs for quick inspection

**Log management**: The script automatically cleans up old logs at the start of each run. Logs are saved with the format `job_{job_id}_{job_name}.txt` in `github_action_logs/`.

**Script usage examples**:
   - `python fetch_github_actions_logs.py` - Check latest runs on release/1.34.0
   - `python fetch_github_actions_logs.py --branch main` - Check main branch
   - `python fetch_github_actions_logs.py --run-id 20108809278` - Get specific run details
   - `python fetch_github_actions_logs.py --all-failed` - Fetch all failed runs

**After fetching logs**, read the saved log files in `github_action_logs/` to identify the failure cause.

**Important**: When debugging E2E test failures or API-related issues:
   - **Always check the uvicorn.log file** - The script automatically downloads artifacts to `run_{run_id}_artifacts/` folder in `github_action_logs/`
   - The uvicorn log is located at: `github_action_logs/run_{run_id}_artifacts/uvicorn.log`
   - The uvicorn log contains server-side errors, API request/response details, and stack traces that may not appear in the main job logs
   - Look for patterns like: `ERROR`, `Exception`, `Traceback`, `500 Internal Server Error`, `AttributeError`, `TypeError`, `Bad Request`, `Payment Required`, etc.
   - Example: `run_20115933676_artifacts/uvicorn.log` contains the server logs for workflow run 20115933676

**If direct API calls are needed**, the script uses the GitHub PAT from the MCP configuration. The script handles authentication automatically.

**PAT Permissions Required**: The PAT must have **"Actions: Read"** (or "Actions: Write") repository permission to access workflow logs.
   - **Fine-grained PATs**: 
     - Repository permission "Actions" with at least "Read" access
     - PAT must have access to the specific repository (not just organization-level)
     - Verify at: https://github.com/settings/tokens → Select your PAT → Check "Repository access" and "Actions" permission
   - **Classic PATs**: `repo` scope or `actions:read` scope
   - **Note**: Logs are stored in Azure Blob Storage and accessed via redirect. If you get 401 errors, verify:
     1. PAT has repository-level "Actions: Read" permission (not just organization-level)
     2. PAT has access to the specific repository
     3. PAT hasn't expired
   - Reference: https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens#repository-permissions-for-actions

Always use this script for GitHub Actions workflow debugging.
```

### Usage

In your IDE AI prompt, just write e.g.:

"check the latest failed github actions and fix all issues"
"check the latest failed github actions and make a plan to thoroughly address and fix each problem"