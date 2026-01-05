# Performance Test Agent - Comprehensive Test Plan

## 1. Introduction

This document outlines the test strategy and test cases for validating the Performance Test Agent. The agent is an autonomous AI engineer that orchestrates end-to-end load testing pipelines using k6, supporting various input formats (Jira, JSON, YAML, HAR, Curl) and providing a GUI for management.

## 2. Test Scope

The scope includes testing all core functionalities:

- Input Ingestion (File & Jira)
- Parsing Logic (OpenAPI, Postman, HAR, Curl)
- Script Generation & Validation
- Execution (Local & Parallel Docker)
- Monitoring (Live Logs & System Health)
- Analysis & Reporting (SLA Evaluation, PDF Reports, Comparison)
- Configuration Management
- GUI Functionality

## 3. Test Environment

- **OS**: Windows/Linux/MacOS
- **Dependencies**: Python 3.x, Node.js (for ESLint), k6, Docker (optional for parallel execution)
- **Browser**: Chrome/Firefox/Edge

## 4. Test Cases

### 4.1. Input Ingestion & Parsing

| ID        | Test Case                | Description                             | Expected Result                          |
| :-------- | :----------------------- | :-------------------------------------- | :--------------------------------------- |
| **TC-01** | Parse JSON Input         | Upload `input.json` with endpoint list. | Script generated with correct endpoints. |
| **TC-02** | Parse YAML Input         | Upload a YAML file with API definition. | Script generated successfully.           |
| **TC-03** | Parse Jira Story         | Provide Jira credentials and issue key. | Description parsed, script generated.    |
| **TC-04** | Parse Postman Collection | Upload `postman_input.json`.            | Postman requests converted to k6 script. |
| **TC-05** | Parse HAR File           | Upload `har_input.json`.                | HAR entries converted to k6 requests.    |
| **TC-06** | Parse Curl Commands      | Upload `curl_input.txt`.                | Curl commands converted to k6 requests.  |
| **TC-07** | Invalid Input Handling   | Upload a malformed JSON file.           | Error message displayed in console.      |

### 4.2. Script Generation & Validation

| ID        | Test Case             | Description                    | Expected Result                               |
| :-------- | :-------------------- | :----------------------------- | :-------------------------------------------- |
| **TC-08** | Generate k6 Script    | Verify `script.js` content.    | Contains correct imports, options, and logic. |
| **TC-09** | Variable Substitution | Use `{{baseUrl}}` in input.    | Replaced with env value in `script.js`.       |
| **TC-10** | Script Linting        | Run pipeline with valid input. | "Script Lint: PASS" in logs.                  |
| **TC-11** | Smoke Run             | Run pipeline.                  | "Smoke Run: PASS" in logs.                    |

### 4.3. Execution & Monitoring

| ID        | Test Case          | Description                              | Expected Result                                   |
| :-------- | :----------------- | :--------------------------------------- | :------------------------------------------------ |
| **TC-12** | Local Execution    | Run pipeline with `parallel=1`.          | Test runs locally, logs stream to console.        |
| **TC-13** | Parallel Execution | Run with `parallel=2` (requires Docker). | 2 Docker containers spawned, results merged.      |
| **TC-14** | Live Metrics       | Check "Live Logs" tab during run.        | Latency, RPS, and VUs charts update in real-time. |
| **TC-15** | System Health      | Check "System Health" tab.               | CPU and Memory bars update dynamically.           |
| **TC-16** | Stop Execution     | Click "Stop Execution" button.           | Process terminates, status updates.               |

### 4.4. Analysis & Reporting

| ID        | Test Case             | Description                          | Expected Result                               |
| :-------- | :-------------------- | :----------------------------------- | :-------------------------------------------- |
| **TC-17** | SLA Evaluation (Pass) | Run test meeting SLA.                | "SLA Passed" in summary.                      |
| **TC-18** | SLA Evaluation (Fail) | Run test failing SLA.                | "SLA Failed" in summary.                      |
| **TC-19** | PDF Report            | Click "PDF Report" link.             | PDF downloads with summary and charts.        |
| **TC-20** | Result Comparison     | Select 2 runs in History -> Compare. | Comparison table displayed in modal.          |
| **TC-21** | Download All          | Click "Download All" for a run.      | Zip file downloaded containing all artifacts. |

### 4.5. Configuration & Management

| ID        | Test Case      | Description                    | Expected Result                        |
| :-------- | :------------- | :----------------------------- | :------------------------------------- |
| **TC-22** | Save Config    | Fill form -> Save as Default.  | `config.yaml` created/updated.         |
| **TC-23** | Load Config    | Refresh page after saving.     | Form fields populated from config.     |
| **TC-24** | Create Profile | Create `staging.yaml` profile. | Profile appears in dropdown.           |
| **TC-25** | Switch Profile | Select profile from dropdown.  | Pipeline runs with profile settings.   |
| **TC-26** | Edit Config    | Edit config via modal.         | Changes saved to file.                 |
| **TC-27** | Reset Config   | Click "Reset Config".          | `config.yaml` deleted, fields cleared. |
| **TC-28** | Show Env Vars  | Click "Show Server Env".       | Env vars displayed (secrets masked).   |

### 4.6. History Management

| ID        | Test Case         | Description                           | Expected Result                             |
| :-------- | :---------------- | :------------------------------------ | :------------------------------------------ |
| **TC-29** | List History      | View History tab.                     | Past runs listed by timestamp.              |
| **TC-30** | Delete Run        | Select run -> Delete.                 | Run folder removed from disk and list.      |
| **TC-31** | Cleanup Threshold | Set threshold < current usage -> Run. | Oldest runs archived/deleted automatically. |

## 5. Automated Verification

Use the provided `verify_agent.py` script to perform a quick sanity check of the core components.

```bash
python verify_agent.py
```

## 6. Manual Verification Steps

### 6.1. Verify Live Monitoring

1. Start server: `python server.py`
2. Open `http://localhost:3000`
3. Load `input.json` (Local File tab)
4. Set Duration to `30s` in JSON
5. Click **Run Pipeline**
6. Switch to **Live Logs** tab
7. **Verify**: Charts (Latency, RPS, VUs) are updating. System Health bars are moving.

### 6.2. Verify Comparison

1. Run the pipeline twice (to generate history).
2. Go to **History** tab.
3. Select the two most recent runs.
4. Click **Compare Selected**.
5. **Verify**: Modal opens with a markdown table comparing metrics.

### 6.3. Verify PDF Report

1. After a run completes, look at the bottom of the Output panel.
2. Click **PDF Report**.
3. **Verify**: PDF opens/downloads containing test summary, SLA verdict, and embedded charts.

### 6.4. Verify Stop Functionality

1. Start a long-running test (e.g., 60s).
2. Click **Stop Execution**.
3. **Verify**: Console shows termination message, process stops.1. After a run completes, look at the bottom of the Output panel.
4. Click **PDF Report**.
5. **Verify**: PDF opens/downloads containing test summary, SLA verdict, and embedded charts.

### 6.4. Verify Stop Functionality

1. Start a long-running test (e.g., 60s).
2. Click **Stop Execution**.
3. **Verify**: Console shows termination message, process stops.
