import requests
import time
import json
import os

BASE_URL = "http://localhost:3000"

def log(msg, status="INFO"):
    print(f"[{status}] {msg}")

def check_server_health():
    log("Checking server health...")
    try:
        res = requests.get(f"{BASE_URL}/api/system-health")
        if res.status_code == 200:
            data = res.json()
            log(f"Server healthy. CPU: {data.get('cpu_percent')}%, Mem: {data.get('memory_percent')}%", "PASS")
            return True
        else:
            log(f"Server returned {res.status_code}", "FAIL")
            return False
    except Exception as e:
        log(f"Failed to connect to server: {e}", "FAIL")
        return False

def test_config_management():
    log("Testing configuration management...")
    
    # 1. Save Config
    payload = {
        "jira": {
            "base_url": "https://test.atlassian.net",
            "issue_key": "TEST-123",
            "auth": "test-token"
        }
    }
    res = requests.post(f"{BASE_URL}/save-config", json=payload)
    if res.status_code != 200:
        log("Failed to save config", "FAIL")
        return False
    
    # 2. Get Config
    res = requests.get(f"{BASE_URL}/get-config")
    data = res.json()
    if data.get("jira_url") == "https://test.atlassian.net":
        log("Config saved and retrieved successfully", "PASS")
    else:
        log(f"Config mismatch: {data}", "FAIL")
        return False

    # 3. Reset Config
    requests.post(f"{BASE_URL}/reset-config")
    res = requests.get(f"{BASE_URL}/get-config")
    if not res.json():
        log("Config reset successfully", "PASS")
    else:
        log("Config reset failed", "FAIL")
        return False
    return True

def test_profile_management():
    log("Testing profile management...")
    
    # Create Profile
    profile_name = "test_profile.yaml"
    content = "jira_url: 'https://profile.test'"
    res = requests.post(f"{BASE_URL}/save-config-raw", json={"filename": profile_name, "content": content})
    
    if res.status_code == 200:
        # List Profiles
        res = requests.get(f"{BASE_URL}/api/profiles")
        profiles = res.json()
        if profile_name in profiles:
            log("Profile created and listed successfully", "PASS")
            return True
        else:
            log("Created profile not found in list", "FAIL")
            return False
    else:
        log("Failed to create profile", "FAIL")
        return False

def test_pipeline_execution():
    log("Testing pipeline execution (Dry Run)...")
    
    input_data = {
        "api_collection": {
            "endpoints": [{"method": "GET", "url": "https://httpbin.org/get"}]
        },
        "sla": {"http_req_duration_p95_ms": 500},
        "workload_scenario": {"executor": "constant-vus", "vus": 1, "duration": "1s"}
    }
    
    payload = {
        "mode": "file",
        "fileContent": input_data,
        "dryRun": True,
        "verbose": True,
        "parallel": 2,
        "cleanupThreshold": 80,
        "webhookUrl": "https://example.com/webhook"
    }
    
    try:
        res = requests.post(f"{BASE_URL}/run", json=payload, stream=True)
        if res.status_code == 200:
            log("Pipeline triggered successfully", "PASS")
            # Consume stream to ensure completion
            for line in res.iter_lines():
                pass
            return True
        else:
            log(f"Pipeline trigger failed: {res.status_code}", "FAIL")
            return False
    except Exception as e:
        log(f"Pipeline execution error: {e}", "FAIL")
        return False

def test_history_and_comparison():
    log("Testing history and comparison...")
    res = requests.get(f"{BASE_URL}/api/history")
    if res.status_code != 200:
        log("Failed to fetch history", "FAIL")
        return False
    
    history = res.json()
    log(f"Found {len(history)} historical runs", "INFO")
    
    if len(history) >= 2:
        folder1 = history[0]['name'] if isinstance(history[0], dict) else history[0]
        folder2 = history[1]['name'] if isinstance(history[1], dict) else history[1]
        log(f"Testing comparison between {folder1} and {folder2}...", "INFO")
        
        payload = {"folder1": folder1, "folder2": folder2}
        res = requests.post(f"{BASE_URL}/compare", json=payload, stream=True)
        if res.status_code == 200:
            log("Comparison triggered successfully", "PASS")
            for line in res.iter_lines():
                pass
        else:
            log(f"Comparison failed: {res.status_code}", "FAIL")
    else:
        log("Not enough history for comparison test (need 2 runs)", "SKIP")
    return True

def test_delete_run():
    log("Testing delete run API...")
    # Try to delete a dummy folder name to verify API reachability
    payload = {"folders": ["non_existent_folder_999"]}
    res = requests.post(f"{BASE_URL}/api/delete-run", json=payload)
    if res.status_code == 200:
        log("Delete run API reachable", "PASS")
        return True
    else:
        log(f"Delete run API failed: {res.status_code}", "FAIL")
        return False

def test_neuro_san_integration():
    log("Testing Neuro-San Studio integration...", "INFO")
    
    # 1. Setup Dummy Neuro-San Environment
    base_dir = os.path.dirname(os.path.abspath(__file__))
    repo_path = os.path.join(base_dir, 'neuro-san-studio')
    script_path = os.path.join(repo_path, 'process.py')
    
    # Ensure directory exists
    if not os.path.exists(repo_path):
        os.makedirs(repo_path)
    
    # Backup existing script if any
    original_content = None
    if os.path.exists(script_path):
        with open(script_path, 'r') as f:
            original_content = f.read()
            
    # Create a dummy process.py that prints a signature
    signature = "NEURO_SAN_VERIFICATION_SIGNATURE_12345"
    dummy_script = f"print('{signature}')"
    
    with open(script_path, 'w') as f:
        f.write(dummy_script)
        
    try:
        # 2. Run Pipeline (Dry Run)
        input_data = {
            "api_collection": {"endpoints": [{"method": "GET", "url": "https://httpbin.org/get"}]},
            "sla": {"http_req_duration_p95_ms": 500},
            "workload_scenario": {"executor": "constant-vus", "vus": 1, "duration": "1s"}
        }
        
        payload = {"mode": "file", "fileContent": input_data, "dryRun": True}
        
        res = requests.post(f"{BASE_URL}/run", json=payload, stream=True)
        if res.status_code != 200:
            log(f"Pipeline failed to start: {res.status_code}", "FAIL")
            return False
            
        # Capture logs for debugging
        logs = []
        for line in res.iter_lines():
            if line:
                decoded = line.decode('utf-8')
                logs.append(decoded)
        
        # Verify Streaming Output
        streaming_verified = False
        for l in logs:
            if "[NEURO-SAN]" in l and signature in l:
                streaming_verified = True
                break
        
        if streaming_verified:
            log("Neuro-San output streaming verified in live logs", "PASS")
        else:
            log("Neuro-San output streaming NOT detected", "FAIL")
        
        # Check if smoke test was skipped (optional verification for environments without k6)
        for l in logs:
            if "Smoke test skipped" in l:
                log("Verified: Smoke test skipped gracefully due to missing k6", "INFO")
                break
            
        # 3. Verify Output in Summary
        res = requests.get(f"{BASE_URL}/api/history")
        latest_run_data = res.json()[0]
        
        # Verify Badge Flag
        if isinstance(latest_run_data, dict) and latest_run_data.get('enhanced') is True:
            log("Neuro-San badge flag (enhanced=True) verified in API", "PASS")
        else:
            log(f"Neuro-San badge flag missing. Response: {latest_run_data}", "FAIL")
            log("=== SERVER LOGS START ===", "DEBUG")
            for l in logs:
                print(l)
            log("=== SERVER LOGS END ===", "DEBUG")

        latest_run = latest_run_data['name'] if isinstance(latest_run_data, dict) else latest_run_data
        summary_res = requests.get(f"{BASE_URL}/results/{latest_run}/summary.md")
        
        if signature in summary_res.text:
            log("Neuro-San output found in summary.md", "PASS")
        else:
            log("Neuro-San output NOT found in summary.md", "FAIL")
            return False
            
        # Verify HTML Report
        report_url = f"{BASE_URL}/results/{latest_run}/neuro_san_report.html"
        report_res = requests.get(report_url)
        if report_res.status_code == 200 and signature in report_res.text:
            log("Neuro-San HTML report generated and accessible", "PASS")
            return True
        else:
            log(f"Neuro-San HTML report missing (Status: {report_res.status_code})", "FAIL")
            return False
    finally:
        # Cleanup: Restore original script or delete dummy
        if original_content:
            with open(script_path, 'w') as f:
                f.write(original_content)
        elif os.path.exists(script_path):
            os.remove(script_path)

def test_neuro_san_failure_and_rerun():
    log("Testing Neuro-San failure flagging and re-run...", "INFO")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    repo_path = os.path.join(base_dir, 'neuro-san-studio')
    script_path = os.path.join(repo_path, 'process.py')
    
    # Ensure directory exists
    if not os.path.exists(repo_path):
        os.makedirs(repo_path)
    
    # Backup existing script if any
    original_content = None
    if os.path.exists(script_path):
        with open(script_path, 'r') as f:
            original_content = f.read()
            
    try:
        # --- Step 1: Failing Script ---
        log("Step 1: Simulating Neuro-San script failure...", "INFO")
        with open(script_path, 'w') as f:
            f.write("import sys; print('CRITICAL FAILURE'); sys.exit(1)")
            
        # Run Pipeline (Dry Run)
        input_data = {
            "api_collection": {"endpoints": [{"method": "GET", "url": "https://httpbin.org/get"}]},
            "sla": {"http_req_duration_p95_ms": 500},
            "workload_scenario": {"executor": "constant-vus", "vus": 1, "duration": "1s"}
        }
        
        payload = {"mode": "file", "fileContent": input_data, "dryRun": True}
        
        res = requests.post(f"{BASE_URL}/run", json=payload)
        if res.status_code != 200:
            log(f"Pipeline failed to start: {res.status_code}", "FAIL")
            return False
            
        # Wait for completion
        for _ in res.iter_lines(): pass
        
        # Verify Failure in History
        res = requests.get(f"{BASE_URL}/api/history")
        latest_run_data = res.json()[0]
        run_name = latest_run_data['name'] if isinstance(latest_run_data, dict) else latest_run_data
        status = latest_run_data.get('status') if isinstance(latest_run_data, dict) else 'UNKNOWN'
        
        if status == 'FAIL':
            log(f"Run {run_name} correctly flagged as FAIL", "PASS")
        else:
            log(f"Run {run_name} status is {status}, expected FAIL", "FAIL")
            return False

        # --- Step 2: Re-run with Success ---
        log("Step 2: Simulating Re-run with fixed script...", "INFO")
        rerun_signature = "ANALYSIS_UPDATED_SUCCESSFULLY"
        with open(script_path, 'w') as f:
            f.write(f"print('{rerun_signature}')")
            
        # Call Re-analyze
        res = requests.post(f"{BASE_URL}/reanalyze", json={"folder": run_name}, stream=True)
        if res.status_code != 200:
            log(f"Re-analyze failed: {res.status_code}", "FAIL")
            return False
            
        # Consume stream
        for _ in res.iter_lines(): pass
        
        # Verify Report Updated
        report_url = f"{BASE_URL}/results/{run_name}/neuro_san_report.html"
        res = requests.get(report_url)
        if res.status_code == 200 and rerun_signature in res.text:
            log("Neuro-San analysis updated successfully after re-run", "PASS")
        else:
            log("Neuro-San analysis did NOT update", "FAIL")
            return False
            
        return True

    finally:
        # Cleanup: Restore original script or delete dummy
        if original_content:
            with open(script_path, 'w') as f:
                f.write(original_content)
        elif os.path.exists(script_path):
            os.remove(script_path)

def test_neuro_san_custom_config():
    log("Testing Neuro-San custom configuration...", "INFO")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    repo_path = os.path.join(base_dir, 'neuro-san-studio')
    script_path = os.path.join(repo_path, 'process.py')
    
    # Ensure directory exists
    if not os.path.exists(repo_path):
        os.makedirs(repo_path)
    
    # Backup existing script if any
    original_content = None
    if os.path.exists(script_path):
        with open(script_path, 'r') as f:
            original_content = f.read()
            
    try:
        # Create script that reads config
        script_content = """
import argparse
import yaml
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--results')
parser.add_argument('--inputs')
parser.add_argument('--raw-results')
parser.add_argument('--config')
args, unknown = parser.parse_known_args()

if args.config:
    try:
        with open(args.config, 'r') as f:
            conf = yaml.safe_load(f)
            print(f"CONFIG_VALUE:{conf.get('test_key')}")
    except Exception as e:
        print(f"Error reading config: {e}")
else:
    print("No config argument received")
"""
        with open(script_path, 'w') as f:
            f.write(script_content)
            
        # Run Pipeline
        input_data = {
            "api_collection": {"endpoints": [{"method": "GET", "url": "https://httpbin.org/get"}]},
            "sla": {"http_req_duration_p95_ms": 500},
            "workload_scenario": {"executor": "constant-vus", "vus": 1, "duration": "1s"},
            "neuro_san_config": {"test_key": "VERIFY_CONFIG_PASS"}
        }
        
        payload = {"mode": "file", "fileContent": input_data, "dryRun": True}
        
        res = requests.post(f"{BASE_URL}/run", json=payload)
        if res.status_code != 200:
            log(f"Pipeline failed to start: {res.status_code}", "FAIL")
            return False
            
        # Wait for completion
        for _ in res.iter_lines(): pass
        
        # Verify Output
        res = requests.get(f"{BASE_URL}/api/history")
        latest_run_data = res.json()[0]
        run_name = latest_run_data['name'] if isinstance(latest_run_data, dict) else latest_run_data
        
        summary_res = requests.get(f"{BASE_URL}/results/{run_name}/summary.md")
        
        if "CONFIG_VALUE:VERIFY_CONFIG_PASS" in summary_res.text:
            log("Custom configuration passed to Neuro-San script successfully", "PASS")
            return True
        else:
            log("Custom configuration verification failed", "FAIL")
            return False

    finally:
        # Restore
        if original_content:
            with open(script_path, 'w') as f:
                f.write(original_content)
        elif os.path.exists(script_path):
            os.remove(script_path)

def test_neuro_san_preflight_failure():
    log("Testing Neuro-San pre-flight failure...", "INFO")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    repo_path = os.path.join(base_dir, 'neuro-san-studio')
    script_path = os.path.join(repo_path, 'preflight.py')
    
    # Ensure directory exists
    if not os.path.exists(repo_path):
        os.makedirs(repo_path)
    
    # Backup existing script if any
    original_content = None
    if os.path.exists(script_path):
        with open(script_path, 'r') as f:
            original_content = f.read()
            
    try:
        # Create failing pre-flight script
        with open(script_path, 'w') as f:
            f.write("import sys; print('PRE-FLIGHT CHECK FAILED'); sys.exit(1)")
            
        # Run Pipeline
        input_data = {
            "api_collection": {"endpoints": [{"method": "GET", "url": "https://httpbin.org/get"}]},
            "sla": {"http_req_duration_p95_ms": 500},
            "workload_scenario": {"executor": "constant-vus", "vus": 1, "duration": "1s"}
        }
        
        payload = {"mode": "file", "fileContent": input_data, "dryRun": True}
        
        res = requests.post(f"{BASE_URL}/run", json=payload)
        # Note: Server returns 200 even if pipeline fails internally, we check history for status
        
        # Wait for completion
        for _ in res.iter_lines(): pass
        
        # Verify Output
        res = requests.get(f"{BASE_URL}/api/history")
        latest_run_data = res.json()[0]
        status = latest_run_data.get('status') if isinstance(latest_run_data, dict) else 'UNKNOWN'
        
        if status == 'PRE-FLIGHT FAILED':
            log("Run correctly flagged as PRE-FLIGHT FAILED", "PASS")
            
            # Verify log file
            log_res = requests.get(f"{BASE_URL}/results/{latest_run_data['name']}/preflight_log.txt")
            if log_res.status_code == 200 and "PRE-FLIGHT CHECK FAILED" in log_res.text:
                log("Pre-flight log file verified", "PASS")
                return True
            else:
                log("Pre-flight log file missing or incorrect", "FAIL")
                return False
        else:
            log(f"Run status is {status}, expected PRE-FLIGHT FAILED", "FAIL")
            return False

def test_csv_data_driving():
    log("Testing CSV data driving capability...", "INFO")
    
    csv_content = "username,password\nuser1,pass1\nuser2,pass2"
    
    input_data = {
        "api_collection": {
            "endpoints": [
                {
                    "method": "POST", 
                    "url": "https://httpbin.org/post",
                    "body": {
                        "user": "{{username}}",
                        "pass": "{{password}}"
                    }
                }
            ]
        },
        "workload_scenario": {"executor": "shared-iterations", "vus": 1, "iterations": 2},
        "test_data": {
            "file": "users.csv",
            "content": csv_content
        }
    }
    
    payload = {"mode": "file", "fileContent": input_data, "dryRun": True}
    
    res = requests.post(f"{BASE_URL}/run", json=payload)
    if res.status_code != 200:
        log(f"Pipeline failed to start: {res.status_code}", "FAIL")
        return False
        
    # Wait for completion
    for _ in res.iter_lines(): pass
    
    # Verify Output
    res = requests.get(f"{BASE_URL}/api/history")
    latest_run_data = res.json()[0]
    run_name = latest_run_data['name'] if isinstance(latest_run_data, dict) else latest_run_data
    
    # Check script.js for CSV logic
    script_res = requests.get(f"{BASE_URL}/results/{run_name}/scripts/script.js")
    if script_res.status_code == 200:
        script_content = script_res.text
        if "SharedArray" in script_content and "papaparse" in script_content and "users.csv" in script_content:
             log("Generated script contains CSV data loading logic", "PASS")
        else:
             log("Generated script missing CSV logic", "FAIL")
             return False
    else:
        log("Failed to retrieve generated script", "FAIL")
        return False

    # Check if CSV file exists
    csv_res = requests.get(f"{BASE_URL}/results/{run_name}/scripts/users.csv")
    if csv_res.status_code == 200 and csv_res.text == csv_content:
        log("CSV file correctly saved in scripts directory", "PASS")
        return True
    else:
        log("CSV file missing or content mismatch", "FAIL")
        return False

    finally:
        # Restore
        if original_content:
            with open(script_path, 'w') as f:
                f.write(original_content)
        elif os.path.exists(script_path):
            os.remove(script_path)

def main():
    print("=== Starting Verification Script ===\n")
    
    if not check_server_health():
        print("\nAborting tests due to server unavailability.")
        return

    test_config_management()
    test_profile_management()
    test_pipeline_execution()
    time.sleep(1.1) # Ensure unique timestamp for second run
    test_pipeline_execution()
    test_history_and_comparison()
    test_delete_run()
    test_neuro_san_integration()
    test_neuro_san_failure_and_rerun()
    test_neuro_san_custom_config()
    test_neuro_san_preflight_failure()
    test_csv_data_driving()
    test_advanced_generator_features()
    
    print("\n=== Verification Complete ===")

if __name__ == "__main__":
    main()