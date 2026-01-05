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
        folder1 = history[0]
        folder2 = history[1]
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
    
    print("\n=== Verification Complete ===")

if __name__ == "__main__":
    main()