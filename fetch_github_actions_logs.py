#!/usr/bin/env python3
"""
Script to fetch GitHub Actions workflow run logs

Usage:
    python _dev/fetch_github_actions_logs.py [--branch BRANCH] [--run-id RUN_ID] [--all-failed] [--per-page N]
"""
import urllib.request
import urllib.parse
import json
import sys
import argparse
import os
import shutil
import re
from pathlib import Path

def load_env_file(env_path=".env"):
    """Load environment variables from a .env file (built-in, no external dependencies)
    
    Args:
        env_path: Path to the .env file (default: .env in current directory)
    """
    env_file = Path(env_path)
    if not env_file.exists():
        return
    
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Parse KEY=VALUE format
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                
                # Only set if not already in environment (env vars take precedence)
                if key and key not in os.environ:
                    os.environ[key] = value

# Load .env file if it exists (in script directory)
script_dir = Path(__file__).parent
load_env_file(script_dir / ".env")

# Load configuration from environment variables
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
OWNER = os.getenv("OWNER")
REPO = os.getenv("REPO")
LOG_DIR = os.getenv("LOG_DIR", "github_action_logs")

# Validate required configuration
if not GITHUB_TOKEN:
    print("[ERROR] GITHUB_TOKEN not found in environment variables or .env file")
    print("[ERROR] Please create a .env file based on .env.example")
    print("[ERROR] Or set GITHUB_TOKEN as an environment variable")
    sys.exit(1)

def make_request(url, params=None, follow_redirects=True, is_redirect=False):
    """Make a GitHub API request
    
    Args:
        url: The URL to request
        params: Query parameters
        follow_redirects: Whether to follow redirects
        is_redirect: True if this is a redirect follow (don't send PAT to non-GitHub URLs)
    """
    if params:
        url += "?" + urllib.parse.urlencode(params)
    
    req = urllib.request.Request(url)
    
    # Only send Authorization header to GitHub API, not to redirects (Azure Blob Storage)
    # Azure redirect URLs already contain SAS tokens for authentication
    is_github_url = 'api.github.com' in url or 'github.com' in url
    
    if not is_redirect or is_github_url:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "Python-GitHub-Actions-Log-Fetcher")
    
    try:
        # For redirects, use an opener that doesn't automatically follow redirects
        if follow_redirects and not is_redirect:
            # Create opener that doesn't follow redirects automatically
            class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    # Store redirect info and return None to prevent automatic redirect
                    self.redirect_code = code
                    self.redirect_url = newurl
                    return None
            
            handler = NoRedirectHandler()
            opener = urllib.request.build_opener(handler)
            try:
                response = opener.open(req)
                # If we get here, no redirect occurred
                use_response = response
            except urllib.error.HTTPError as e:
                # Check if this is a redirect (3xx status)
                if e.code in (301, 302, 303, 307, 308):
                    redirect_url = e.headers.get('Location') or handler.redirect_url
                    if redirect_url:
                        # Follow redirect without sending PAT (Azure URL has SAS token)
                        return make_request(redirect_url, follow_redirects=False, is_redirect=True)
                raise
            else:
                use_response = response
        else:
            # For non-redirects or already following redirect, use normal opener
            use_response = urllib.request.urlopen(req)
        
        with use_response:
            
            content_type = use_response.headers.get_content_type()
            if content_type and 'application/json' in content_type:
                return json.loads(use_response.read().decode('utf-8'))
            else:
                # For logs, the response is plain text (may be gzipped)
                content = use_response.read()
                # Try to decompress if gzipped
                if use_response.headers.get('Content-Encoding') == 'gzip':
                    import gzip
                    content = gzip.decompress(content)
                # Decode with error handling for encoding issues
                # Remove BOM if present
                if content.startswith(b'\xef\xbb\xbf'):
                    content = content[3:]
                return content.decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        # Provide more detailed error information
        error_body = ""
        if e.fp:
            try:
                error_body = e.read().decode('utf-8', errors='replace')
            except:
                error_body = "Could not read error response"
        
        # For 401/403 errors, provide helpful guidance
        if e.code in (401, 403):
            error_msg = f"{e.reason}"
            if error_body:
                try:
                    error_json = json.loads(error_body)
                    if 'message' in error_json:
                        error_msg += f": {error_json['message']}"
                except:
                    # Safely truncate error body, removing any problematic characters
                    safe_body = error_body[:200].encode('ascii', errors='replace').decode('ascii')
                    error_msg += f": {safe_body}"
        else:
            # Safely encode error message for Windows console
            safe_body = error_body[:200].encode('ascii', errors='replace').decode('ascii')
            error_msg = f"{e.reason}. Response: {safe_body}"
        
        raise urllib.error.HTTPError(e.url, e.code, error_msg, e.headers, e.fp)

def get_workflow_runs(branch=None, per_page=5):
    """Get workflow runs for the repository"""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/runs"
    params = {"per_page": per_page}
    if branch:
        params["branch"] = branch
    
    return make_request(url, params)

def get_workflow_run(run_id):
    """Get details of a specific workflow run"""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/runs/{run_id}"
    return make_request(url)

def get_workflow_jobs(run_id):
    """Get jobs for a workflow run"""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/runs/{run_id}/jobs"
    return make_request(url)

def get_job_logs(job_id):
    """Get logs for a specific job
    
    Note: This endpoint requires 'Actions: Read' permission on the repository.
    The logs endpoint may return a redirect to the actual log file location.
    
    For fine-grained PATs: Repository permission "Actions" with at least "Read" access
    For classic PATs: 'repo' scope or 'actions:read' scope
    """
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/jobs/{job_id}/logs"
    try:
        # The logs endpoint returns the raw log content (may be gzipped)
        return make_request(url, follow_redirects=True)
    except urllib.error.HTTPError as e:
        # Log the actual error for debugging
        error_detail = str(e)
        if e.code == 401:
            print(f"   [DEBUG] 401 Unauthorized - {error_detail}")
            return None  # Authentication issue - PAT may need 'Actions: Read' permission
        elif e.code == 403:
            print(f"   [DEBUG] 403 Forbidden - {error_detail}")
            return None  # Forbidden - PAT may not have sufficient permissions
        elif e.code == 404:
            print(f"   [DEBUG] 404 Not Found - Logs may have expired")
            return None  # Logs may have expired (logs are retained for 90 days)
        else:
            print(f"   [DEBUG] HTTP {e.code} - {error_detail}")
            raise

def get_run_artifacts(run_id):
    """Get artifacts for a workflow run"""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/runs/{run_id}/artifacts"
    try:
        return make_request(url)
    except urllib.error.HTTPError as e:
        print(f"   [DEBUG] Error fetching artifacts: HTTP {e.code}")
        return None

def download_artifact(artifact_id, artifact_name, run_id):
    """Download and extract an artifact
    
    Args:
        artifact_id: The artifact ID
        artifact_name: The artifact name
        run_id: The workflow run ID (for logging)
        
    Returns:
        True if successful, False otherwise
    """
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/artifacts/{artifact_id}/zip"
    
    try:
        print(f"   Downloading artifact '{artifact_name}'...")
        
        # Download the artifact (it's a ZIP file - binary data)
        # We need to use urllib directly to get binary data, not our make_request wrapper
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "Python-GitHub-Actions-Log-Fetcher")
        
        # Handle redirect for artifact download
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                self.redirect_url = newurl
                return None
        
        handler = NoRedirectHandler()
        opener = urllib.request.build_opener(handler)
        
        try:
            response = opener.open(req)
            zip_data = response.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                # Follow redirect without auth header (Azure storage has its own auth)
                redirect_url = e.headers.get('Location') or handler.redirect_url
                if redirect_url:
                    req2 = urllib.request.Request(redirect_url)
                    with urllib.request.urlopen(req2) as response:
                        zip_data = response.read()
                else:
                    raise
            else:
                raise
        
        # Save to temporary file and extract
        import zipfile
        import io
        
        # Ensure log directory exists
        log_path = Path(LOG_DIR)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # Create a subdirectory for this run's artifacts
        artifact_dir = log_path / f"run_{run_id}_artifacts"
        artifact_dir.mkdir(exist_ok=True)
        
        # Extract the ZIP
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(artifact_dir)
            extracted_files = zf.namelist()
            
        print(f"   [OK] Artifact extracted to: {artifact_dir}")
        print(f"   [INFO] Extracted files: {', '.join(extracted_files)}")
        
        # Note: All artifacts (uvicorn.log, .pytest_cache, etc.) are in the artifacts folder
        # Access uvicorn.log at: {artifact_dir}/uvicorn.log
        
        return True
        
    except zipfile.BadZipFile:
        print(f"   [WARN] Artifact is not a valid ZIP file")
        return False
    except urllib.error.HTTPError as e:
        error_detail = str(e)
        if e.code == 410:
            print(f"   [INFO] Artifact has expired (artifacts are retained for 90 days)")
        else:
            print(f"   [WARN] Error downloading artifact: HTTP {e.code} - {error_detail}")
        return False
    except Exception as e:
        print(f"   [ERROR] Unexpected error downloading artifact: {e}")
        import traceback
        traceback.print_exc()
        return False

def cleanup_log_directory():
    """Clean up existing logs and artifact directories in the log directory"""
    log_path = Path(LOG_DIR)
    if log_path.exists():
        # Remove all files and directories in the log directory
        for item in log_path.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                print(f"   [WARN] Could not delete {item.name}: {e}")
    else:
        # Create directory if it doesn't exist
        log_path.mkdir(parents=True, exist_ok=True)

def analyze_log_errors(logs):
    """Analyze GitHub Actions log to extract error information
    
    Args:
        logs: The log content as a string
        
    Returns:
        A summary string with error information
    """
    lines = logs.split('\n')
    summary_lines = []
    summary_lines.append("=" * 80)
    summary_lines.append("ERROR SUMMARY - Issues Found in This Workflow Run")
    summary_lines.append("=" * 80)
    summary_lines.append("")
    
    current_step = None
    step_stack = []  # Track nested step groups
    failed_steps = []
    error_messages = []
    in_error_section = False
    error_context = []
    exit_codes = []
    
    for i, line in enumerate(lines):
        # Detect step groups (can be nested)
        if '##[group]' in line:
            step_match = re.search(r'##\[group\](.+)', line)
            if step_match:
                step_name = step_match.group(1).strip()
                step_stack.append(step_name)
                current_step = step_name
        
        # Detect end of step groups
        if '##[endgroup]' in line:
            if step_stack:
                step_stack.pop()
                current_step = step_stack[-1] if step_stack else None
        
        # Detect step commands
        if '##[command]' in line:
            cmd_match = re.search(r'##\[command\](.+)', line)
            if cmd_match:
                cmd = cmd_match.group(1).strip()
                # Use command as step context if no group is active
                if not current_step:
                    current_step = f"Command: {cmd[:50]}"
        
        # Detect errors
        if '##[error]' in line:
            in_error_section = True
            error_text = line.replace('##[error]', '').strip()
            if error_text:
                error_messages.append(error_text)
                if current_step:
                    step_name = current_step
                    # Check if step already in failed_steps
                    step_found = False
                    for step in failed_steps:
                        if step['name'] == step_name:
                            step_found = True
                            step['errors'].append(error_text)
                            break
                    if not step_found:
                        failed_steps.append({'name': step_name, 'errors': [error_text]})
                error_context = [error_text]
        
        # Collect error context (lines after ##[error])
        elif in_error_section:
            if line.strip() and not (line.startswith('##') or line.startswith('2025-')):
                error_context.append(line.strip())
                if len(error_context) > 8:  # Limit context
                    in_error_section = False
            elif '##[group]' in line or '##[endgroup]' in line:
                in_error_section = False
        
        # Detect exit codes and failures
        if 'Process completed with exit code' in line:
            exit_code_match = re.search(r'exit code (\d+)', line)
            if exit_code_match:
                code = exit_code_match.group(1)
                exit_codes.append((code, current_step))
                if code != '0':
                    if current_step:
                        step_name = current_step
                        step_found = False
                        for step in failed_steps:
                            if step['name'] == step_name:
                                step_found = True
                                break
                        if not step_found:
                            failed_steps.append({'name': step_name, 'errors': [f"Process exited with code {code}"]})
        
        # Detect tracebacks (improved detection - catches "Traceback" anywhere in line)
        if 'Traceback' in line or 'traceback' in line.lower():
            if current_step:
                step_name = current_step
                step_found = False
                for step in failed_steps:
                    if step['name'] == step_name:
                        step_found = True
                        break
                if not step_found:
                    failed_steps.append({'name': step_name, 'errors': []})
            
            # Collect traceback (usually 10-30 lines)
            tb_lines = [line]
            exception_found = False
            for j in range(i + 1, min(i + 40, len(lines))):
                next_line = lines[j]
                tb_lines.append(next_line)
                
                # Stop at the exception message (various formats)
                if (re.match(r'^\s*\w+Error:', next_line) or 
                    re.match(r'^\s*\w+Exception:', next_line) or
                    re.match(r'^\s*\w+Warning:', next_line) or
                    re.search(r'Error:|Exception:|Warning:', next_line)):
                    exception_found = True
                    # Try to get a few more lines after exception for context
                    for k in range(j + 1, min(j + 5, len(lines))):
                        if lines[k].strip() and not lines[k].strip().startswith('##'):
                            tb_lines.append(lines[k])
                        else:
                            break
                    break
                
                # Stop if we hit a new step or command (but allow continuation if it's part of traceback)
                if '##[' in next_line and 'Traceback' not in next_line and 'File "' not in next_line:
                    # Only break if we haven't found the exception yet and it's clearly a new section
                    if not exception_found and not any('File "' in prev_line for prev_line in tb_lines[-3:]):
                        break
            
            if tb_lines:
                tb_text = '\n'.join(tb_lines[:30])  # Increased to 30 lines for more context
                error_messages.append(tb_text)
                # Add to step errors if we have a current step
                if current_step:
                    for step in failed_steps:
                        if step['name'] == current_step:
                            # Store more of the traceback (first 1000 chars for better context)
                            tb_preview = tb_text[:1000]
                            if tb_preview not in step['errors']:
                                step['errors'].append(tb_preview)
                            break
        
        # Detect test failures
        if re.search(r'(FAILED|ERROR|FAIL)', line) and ('test' in line.lower() or 'pytest' in line.lower()):
            if current_step:
                step_name = current_step
                step_found = False
                for step in failed_steps:
                    if step['name'] == step_name:
                        step_found = True
                        if line.strip() not in step['errors']:
                            step['errors'].append(line.strip()[:200])
                        break
                if not step_found:
                    failed_steps.append({'name': step_name, 'errors': [line.strip()[:200]]})
    
    # Build summary
    if failed_steps:
        summary_lines.append("FAILED STEPS:")
        summary_lines.append("-" * 80)
        for step in failed_steps:
            summary_lines.append(f"  • {step['name']}")
            if step['errors']:
                # Show first error for each step
                first_error = step['errors'][0]
                error_preview = first_error.replace('\n', ' ').strip()[:250]
                summary_lines.append(f"    Error: {error_preview}")
                if len(step['errors']) > 1:
                    summary_lines.append(f"    ({len(step['errors'])} more error(s) - see full log)")
        summary_lines.append("")
    
    if error_messages:
        summary_lines.append("KEY ERROR MESSAGES:")
        summary_lines.append("-" * 80)
        for i, error in enumerate(error_messages[:5], 1):  # Top 5 errors
            error_clean = error.replace('\n', ' ').strip()[:300]
            if len(error) > 300:
                error_clean += "..."
            summary_lines.append(f"  {i}. {error_clean}")
        summary_lines.append("")
    
    # Look for common failure patterns
    failure_patterns = {
        r'ImportError|cannot import': 'Import error detected',
        r'AttributeError|has no attribute': 'Attribute error detected',
        r'TypeError|unsupported operand': 'Type error detected',
        r'ValueError|invalid value': 'Value error detected',
        r'ConnectionError|Connection refused|Connection timeout': 'Connection error detected',
        r'TimeoutError|timed out': 'Timeout error detected',
        r'AssertionError|assert.*failed': 'Test assertion failed',
        r'HTTPError|HTTP \d+': 'HTTP error detected',
        r'Database error|database.*error|psql.*error': 'Database error detected',
        r'Test.*failed|pytest.*FAILED': 'Test failure detected',
    }
    
    detected_patterns = []
    logs_lower = logs.lower()
    for pattern, description in failure_patterns.items():
        if re.search(pattern, logs_lower, re.IGNORECASE):
            detected_patterns.append(description)
    
    if detected_patterns:
        summary_lines.append("DETECTED ISSUE TYPES:")
        summary_lines.append("-" * 80)
        for pattern in detected_patterns:
            summary_lines.append(f"  • {pattern}")
        summary_lines.append("")
    
    if exit_codes:
        non_zero = [ec for ec in exit_codes if ec[0] != '0']
        if non_zero:
            summary_lines.append("NON-ZERO EXIT CODES:")
            summary_lines.append("-" * 80)
            for code, step in non_zero[:5]:  # Show first 5
                step_name = step if step else "Unknown step"
                summary_lines.append(f"  • Exit code {code} in: {step_name}")
            summary_lines.append("")
    
    if not failed_steps and not error_messages and not exit_codes:
        summary_lines.append("No explicit errors detected in log format.")
        summary_lines.append("Check the full log below for details.")
        summary_lines.append("")
    
    summary_lines.append("=" * 80)
    summary_lines.append("Full log follows below:")
    summary_lines.append("=" * 80)
    summary_lines.append("")
    
    return '\n'.join(summary_lines)

def fetch_and_save_logs(job_id, job_name, run_id=None, show_preview=True):
    """Fetch logs for a job and save to file
    
    Args:
        job_id: The job ID
        job_name: The job name
        run_id: The workflow run ID (required for generating correct log URL)
        show_preview: Whether to show log preview
    """
    print(f"\n   Fetching logs for failed job {job_id}...")
    try:
        logs = get_job_logs(job_id)
        if logs is None:
            print(f"   [WARN] Could not fetch logs. Possible reasons:")
            print(f"          - PAT needs 'Actions: Read' repository permission (fine-grained PAT)")
            print(f"          - PAT needs 'actions:read' scope (classic PAT)")
            print(f"          - Logs may have expired (retained for 90 days)")
            print(f"          - Repository access may be restricted")
            if run_id:
                print(f"   [INFO] View logs directly at: https://github.com/{OWNER}/{REPO}/actions/runs/{run_id}/job/{job_id}")
            else:
                print(f"   [INFO] View logs at: https://github.com/{OWNER}/{REPO}/actions")
            print(f"   [INFO] Verify PAT permissions at: https://github.com/settings/tokens")
            return
        
        # Ensure log directory exists and is clean
        log_path = Path(LOG_DIR)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # Analyze logs to extract error information
        error_summary = analyze_log_errors(logs)
        
        # Save logs to file with error summary at the top (will replace if exists)
        safe_name = job_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        log_file = log_path / f"job_{job_id}_{safe_name}.txt"
        with open(log_file, 'w', encoding='utf-8', errors='replace') as f:
            # Write error summary first
            f.write(error_summary)
            # Then write the full log
            f.write(logs)
        print(f"   [OK] Logs saved to: {log_file}")
        print(f"   [INFO] Error summary added at the top of the log file")
        
        if show_preview:
            print(f"   Last 50 lines of logs:")
            print("   " + "-" * 76)
            # Handle encoding issues on Windows console
            import sys
            for line in logs.split('\n')[-50:]:
                if line.strip():
                    try:
                        print(f"   {line}")
                    except UnicodeEncodeError:
                        # Fallback: encode with error handling for Windows console
                        safe_line = line.encode('ascii', errors='replace').decode('ascii')
                        print(f"   {safe_line}")
    except Exception as e:
        # Safely print error message (avoid encoding issues)
        error_msg = str(e).encode('ascii', errors='replace').decode('ascii')
        print(f"   [ERROR] Error fetching logs: {error_msg}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Fetch GitHub Actions workflow run logs')
    parser.add_argument('--branch', default='release/1.33.0', help='Branch to check (default: release/1.33.0)')
    parser.add_argument('--run-id', help='Specific workflow run ID to fetch')
    parser.add_argument('--all-failed', action='store_true', help='Fetch all failed runs (not just latest push)')
    parser.add_argument('--per-page', type=int, default=3, help='Number of runs to fetch per page (default: 3, since each push has 3 workflows)')
    parser.add_argument('--no-preview', action='store_true', help='Skip showing log preview')
    parser.add_argument('--keep-old-logs', action='store_true', help='Keep existing logs (do not clean up)')
    parser.add_argument('--all-jobs', action='store_true', help='Fetch logs for all jobs (including successful ones)')
    
    args = parser.parse_args()
    
    branch = args.branch
    run_id = args.run_id
    # Default to 3 since each push triggers 3 workflows (e2e, backend, ci)
    # If all-failed is specified, fetch more to get historical failures
    per_page = args.per_page if not args.all_failed else max(args.per_page, 10)
    
    # Clean up old logs at the start of each run (unless --keep-old-logs is specified)
    if not args.keep_old_logs:
        print("Cleaning up old logs...")
        cleanup_log_directory()
        print("Ready to fetch new logs.\n")
    
    print(f"Fetching workflow runs for branch: {branch}")
    print("=" * 80)
    
    # Get recent workflow runs
    runs = get_workflow_runs(branch=branch, per_page=per_page)
    print(f"\nFound {runs['total_count']} total runs")
    print(f"Showing {len(runs['workflow_runs'])} runs:\n")
    
    failed_runs = []
    for run in runs['workflow_runs']:
        status_icon = "[FAIL]" if run['conclusion'] == 'failure' else "[PASS]" if run['conclusion'] == 'success' else "[RUN]"
        print(f"{status_icon} Run #{run['id']}: {run['name']} - {run['conclusion']} ({run['status']})")
        print(f"   Branch: {run['head_branch']}, Commit: {run['head_sha'][:7]}")
        print(f"   URL: {run['html_url']}")
        print()
    
        if run['conclusion'] == 'failure':
            failed_runs.append(run)
    
    # Process specific run or all failed runs
    runs_to_process = []
    if run_id:
        runs_to_process = [r for r in runs['workflow_runs'] if str(r['id']) == str(run_id)]
        if not runs_to_process:
            # Try to fetch the run even if not in the list
            try:
                run_details = get_workflow_run(run_id)
                runs_to_process = [run_details]
            except Exception as e:
                print(f"\n[ERROR] Could not fetch run {run_id}: {e}")
                sys.exit(1)
    elif args.all_failed:
        runs_to_process = failed_runs
    else:
        # Default: process all failed runs from the latest push
        # Each push triggers 3 workflows (e2e, backend, ci), so get all failed runs from the latest commit
        if failed_runs:
            # Group by commit SHA to get all workflows from the same push
            latest_commit = failed_runs[0]['head_sha']
            runs_to_process = [run for run in failed_runs if run['head_sha'] == latest_commit]
            if len(runs_to_process) > 0:
                print(f"\n[INFO] Found {len(runs_to_process)} failed workflow(s) from latest push (commit: {latest_commit[:7]})")
    
    # Process each run
    for run in runs_to_process:
        print(f"\n{'=' * 80}")
        print(f"Fetching details for run ID: {run['id']}")
        print("=" * 80)
        
        if 'name' not in run:
            run = get_workflow_run(run['id'])
        
        print(f"\nWorkflow: {run['name']}")
        print(f"Status: {run['status']}")
        print(f"Conclusion: {run['conclusion']}")
        print(f"Branch: {run['head_branch']}")
        print(f"Commit: {run['head_sha']}")
        print(f"URL: {run['html_url']}")
        
        # Get jobs for this run
        jobs = get_workflow_jobs(run['id'])
        print(f"\n{'=' * 80}")
        print(f"Jobs in this run ({len(jobs['jobs'])}):")
        print("=" * 80)
        
        for job in jobs['jobs']:
            status_icon = "[FAIL]" if job['conclusion'] == 'failure' else "[PASS]" if job['conclusion'] == 'success' else "[RUN]"
            print(f"\n{status_icon} Job #{job['id']}: {job['name']}")
            print(f"   Status: {job['status']}, Conclusion: {job['conclusion']}")
            print(f"   Started: {job['started_at']}")
            print(f"   Completed: {job['completed_at']}")
            print(f"   URL: {job['html_url']}")
            
            # Get logs for failed jobs (or all jobs if --all-jobs is specified)
            if job['conclusion'] == 'failure' or (args.all_jobs and job['conclusion'] == 'success'):
                fetch_and_save_logs(job['id'], job['name'], run_id=run['id'], show_preview=not args.no_preview)
        
        # Fetch artifacts for this run (e.g., uvicorn.log, pytest cache, etc.)
        print(f"\n{'=' * 80}")
        print(f"Checking for artifacts...")
        print("=" * 80)
        
        artifacts_data = get_run_artifacts(run['id'])
        if artifacts_data and 'artifacts' in artifacts_data:
            artifacts = artifacts_data['artifacts']
            if artifacts:
                print(f"\nFound {len(artifacts)} artifact(s):")
                for artifact in artifacts:
                    print(f"\n   • {artifact['name']}")
                    print(f"     Size: {artifact['size_in_bytes']:,} bytes")
                    print(f"     Expired: {artifact['expired']}")
                    print(f"     Created: {artifact['created_at']}")
                    
                    if not artifact['expired']:
                        # Download artifact
                        download_artifact(artifact['id'], artifact['name'], run['id'])
                    else:
                        print(f"     [INFO] Artifact has expired (retained for 90 days)")
            else:
                print("\n   [INFO] No artifacts found for this run")
                print("   [INFO] This is normal if the run failed before artifacts were uploaded")
        else:
            print("\n   [INFO] No artifacts available for this run")
            print("   [INFO] This is normal if the run failed early or didn't produce artifacts")

