import argparse
import os
import sys
import json
import subprocess
import shutil
import yaml
import requests
import threading
import math
import time
from datetime import datetime

try:
    from google import genai
except ImportError:
    genai = None

from parser import parse_input
from generator import generate_k6_script
from sla import evaluate_sla
from report_generator import generate_pdf_report

def log_comm(sender, receiver, message):
    print(f"[AGENT_COMM] {sender}|{receiver}|{message}", flush=True)

def compare_results(folder1, folder2):
    print(f"[STATUS] Comparing results: {folder1} vs {folder2}", flush=True)
    
    def load_metrics(folder):
        # Check direct path
        path = os.path.join(folder, 'summary_export.json')
        if not os.path.exists(path):
            # Check inside ./results if not found
            alt_path = os.path.join('./results', folder, 'summary_export.json')
            if os.path.exists(alt_path):
                path = alt_path
            else:
                print(f"[ERROR] Summary file not found in {folder} or ./results/{folder}", flush=True)
                sys.exit(1)
        
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to load summary from {path}: {e}", flush=True)
            sys.exit(1)

    m1 = load_metrics(folder1)
    m2 = load_metrics(folder2)

    def get_val(metrics, key, subkey):
        try:
            return metrics['metrics'][key]['values'][subkey]
        except KeyError:
            return 0.0

    lines = []
    lines.append(f"# Comparison Report: {folder1} vs {folder2}")
    lines.append("")
    lines.append(f"| {'Metric':<25} | {'Run 1':<15} | {'Run 2':<15} | {'Diff':<20} |")
    lines.append(f"|{'-'*27}|{'-'*17}|{'-'*17}|{'-'*22}|")

    metrics_to_compare = [
        ("RPS (req/s)", "http_reqs", "rate"),
        ("P95 Latency (ms)", "http_req_duration", "p(95)"),
        ("Avg Latency (ms)", "http_req_duration", "avg"),
        ("Error Rate", "http_req_failed", "rate")
    ]

    for label, key, subkey in metrics_to_compare:
        v1 = get_val(m1, key, subkey)
        v2 = get_val(m2, key, subkey)
        
        diff = v2 - v1
        pct = (diff / v1 * 100) if v1 != 0 else 0.0
        
        lines.append(f"| {label:<25} | {v1:<15.4f} | {v2:<15.4f} | {diff:<+10.4f} ({pct:+.2f}%) |")

    report = "\n".join(lines)
    print(report, flush=True)

    output_path = "comparison_report.md"
    try:
        with open(output_path, 'w') as f:
            f.write(report)
        print(f"\n[STATUS] Comparison report saved to {output_path}", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to save comparison report: {e}", flush=True)

def sanitize_dict(d):
    if not isinstance(d, dict):
        return d
    
    sanitized = {}
    for k, v in d.items():
        if any(s in k.lower() for s in ['auth', 'token', 'secret', 'password']):
            sanitized[k] = '****'
        elif isinstance(v, dict):
            sanitized[k] = sanitize_dict(v)
        elif isinstance(v, list):
            sanitized[k] = [sanitize_dict(i) for i in v]
        else:
            sanitized[k] = v
    return sanitized

class PipelineContext:
    def __init__(self, args):
        self.args = args
        self.inputs = None
        self.result_dir = None
        self.timestamp = None
        self.script_path = None
        self.summary_path = None
        self.summary_export_path = None
        self.sla_results = None
        self.summary_md = None
        self.result_files = []
        self.running_processes = []
        self.config = {}

    def log_verbose(self, msg):
        if self.args.verbose:
            print(f"[VERBOSE] {msg}", flush=True)

class IngestionAgent:
    def run(self, context: PipelineContext):
        log_comm("IngestionAgent", "MasterAgent", "Analyzing input source...")
        print("[STATUS] Stage: Input Ingestion", flush=True)
        
        # Determine config file path
        config_file = context.args.config
        if context.args.profile:
            config_file = f"{context.args.profile}.yaml"

        # Load config file if exists
        config_defaults = {}
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    content = f.read()
                    expanded_content = os.path.expandvars(content)
                    config_defaults = yaml.safe_load(expanded_content) or {}
                context.config = config_defaults
            except Exception as e:
                print(f"[WARN] Failed to load config file: {e}", flush=True)

        # Helper to resolve argument (CLI > Config > Default)
        def get_arg(name, default=None):
            val = getattr(context.args, name, None)
            if val is not None and val != "" and val is not False:
                return val
            if name in config_defaults:
                return config_defaults[name]
            return default

        # Resolve generic args
        context.args.output_dir = get_arg('output_dir', './results')

        # Setup Directory
        if context.args.clean and os.path.exists(context.args.output_dir):
            print(f"[STATUS] Cleaning output directory: {context.args.output_dir}", flush=True)
            shutil.rmtree(context.args.output_dir)

        context.timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        context.result_dir = os.path.join(context.args.output_dir, context.timestamp)
        os.makedirs(context.result_dir, exist_ok=True)

        print(f"[STATUS] Starting Agent. Results: {context.result_dir}", flush=True)

        # Determine input source
        final_jira = None
        final_file = None

        if context.args.jira_key:
            final_jira = {
                "base_url": get_arg('jira_url'),
                "issue_key": get_arg('jira_key'),
                "auth": get_arg('jira_auth')
            }
        elif context.args.file:
            final_file = context.args.file
        else:
            if config_defaults.get('jira_key'):
                final_jira = {
                    "base_url": config_defaults.get('jira_url'),
                    "issue_key": config_defaults.get('jira_key'),
                    "auth": config_defaults.get('jira_auth')
                }
            elif config_defaults.get('file'):
                final_file = config_defaults.get('file')

        config = {
            "jira": final_jira,
            "file": final_file
        }
        context.log_verbose(f"Configuration: {json.dumps(sanitize_dict(config), default=str)}")

        parse_res = parse_input(config)
        context.inputs = parse_res['result']
        diagnostics = parse_res['diagnostics']

        if not context.inputs and diagnostics:
            print("\n[AGENT] I'm having trouble understanding your input. Here's what I found:", flush=True)
            for diag in diagnostics:
                print(f"  - Issue at {diag.get('path', 'unknown')}: {diag.get('reason', 'Unknown error')}", flush=True)
            print("\n[AGENT] Please check your input format. I support JSON, YAML, Jira keys, HAR files, and Curl commands.", flush=True)
            print("[AGENT] If you're using a file, make sure the path is correct and the content is valid.", flush=True)
            sys.exit(1)

        context.log_verbose(f"Parsed Input Keys: {list(context.inputs.keys()) if context.inputs else 'None'}")

        with open(os.path.join(context.result_dir, 'input_snapshot.json'), 'w') as f:
            json.dump(context.inputs, f, indent=2)

        # API Collection Validation
        print("[STATUS] Stage: API Collection Validation", flush=True)
        if not context.inputs.get('api_collection'):
            print("\n[AGENT] I couldn't find any API endpoints to test.", flush=True)
            print("[AGENT] Please provide an 'api_collection' in your input, or a list of endpoints.", flush=True)
            print("[AGENT] Example: { \"api_collection\": { \"endpoints\": [...] } }", flush=True)
            sys.exit(1)
        
        print(f"[STATUS] API Collection detected: {list(context.inputs['api_collection'].keys())[0]}", flush=True)
        log_comm("IngestionAgent", "MasterAgent", "Input ingestion and validation successful.")

class GeneratorAgent:
    def run(self, context: PipelineContext):
        log_comm("GeneratorAgent", "MasterAgent", "Generating k6 script from inputs...")
        print("[STATUS] Stage: k6 Script Generation", flush=True)
        script_content = generate_k6_script(context.inputs)
        context.log_verbose(f"Generated script size: {len(script_content)} bytes")

        # Create a dedicated scripts folder
        scripts_dir = os.path.join(context.result_dir, 'scripts')
        os.makedirs(scripts_dir, exist_ok=True)

        # Generate a more descriptive script name
        input_source = ""
        if context.args.file:
            input_source = os.path.basename(context.args.file)
        elif context.args.jira_key:
            input_source = context.args.jira_key

        script_name = "script.js"
        if input_source:
            script_name = f"{os.path.splitext(input_source)[0]}.js"

        context.script_path = os.path.join(scripts_dir, script_name)
        with open(context.script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        log_comm("GeneratorAgent", "MasterAgent", f"Script generated at {context.script_path}")

class ValidationAgent:
    def run(self, context: PipelineContext):
        log_comm("ValidationAgent", "MasterAgent", "Validating script (Lint + Smoke Test)...")
        print("[STATUS] Stage: Script Validation", flush=True)
        # ESLint
        try:
            cmd = ['npx', 'eslint', '--fix', context.script_path]
            context.log_verbose(f"Executing: {' '.join(cmd)}")
            lint_res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', check=False)
            if lint_res.returncode != 0:
                print("\n[AGENT] The generated script has some issues.", flush=True)
                print("[AGENT] I tried to fix them automatically, but some errors remain:", flush=True)
                print(lint_res.stdout, flush=True)
                print("[AGENT] This might be due to invalid characters or structure in your input data.", flush=True)
                log_comm("ValidationAgent", "MasterAgent", "Linting failed.")
                with open(os.path.join(context.result_dir, 'lint_report.txt'), 'w', encoding='utf-8') as f:
                    f.write(lint_res.stdout)
                sys.exit(1)
        except Exception as e:
            print(f"[WARN] Linting skipped or failed to run: {e}", flush=True)

        # Smoke Run
        print("[STATUS] Stage: Smoke Run", flush=True)
        cmd = ['k6', 'run', '--vus', '1', '--duration', '1s', context.script_path]
        context.log_verbose(f"Executing: {' '.join(cmd)}")
        smoke_res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', check=False)
        with open(os.path.join(context.result_dir, 'smoke_run_output.txt'), 'w', encoding='utf-8') as f:
            f.write(smoke_res.stdout + smoke_res.stderr)
        
        if smoke_res.returncode != 0:
            print("\n[AGENT] The test script failed during a quick smoke test.", flush=True)
            print("[AGENT] This usually means there's a runtime error in the script logic.", flush=True)
            print("[AGENT] Please check the 'Smoke Log' artifact for detailed error messages.", flush=True)
            log_comm("ValidationAgent", "MasterAgent", "Smoke test failed.")
            sys.exit(1)

        # Workload Scenario Check
        if not context.inputs.get('workload_scenario'):
            print("[INFO] No workload scenario provided. Using defaults/TODOs.", flush=True)

        # SLA Check
        print("[STATUS] Stage: SLA Check", flush=True)
        if not context.inputs.get('sla'):
            print("\n[AGENT] I need to know your success criteria (SLA) to run the test.", flush=True)
            print("[AGENT] Please add an 'sla' section to your input. For example: { \"sla\": { \"http_req_duration_p95_ms\": 500 } }", flush=True)
            log_comm("ValidationAgent", "MasterAgent", "SLA missing. Aborting.")
            sys.exit(1)
        log_comm("ValidationAgent", "MasterAgent", "Validation passed. Ready for execution.")

class ExecutionAgent:
    def run(self, context: PipelineContext):
        log_comm("ExecutionAgent", "MasterAgent", "Preparing execution environment...")
        context.sla_results = {"pass": None, "verdicts": {}}
        context.summary_path = os.path.join(context.result_dir, 'test_results.json')
        context.summary_export_path = os.path.join(context.result_dir, 'summary_export.json')
        context.result_files = []
        context.running_processes = []
        
        if not context.args.dry_run:
            print("[STATUS] Stage: Full Test Execution", flush=True)
            
            if context.args.parallel and context.args.parallel > 1:
                print(f"[STATUS] Parallel Execution: Spawning {context.args.parallel} Docker containers", flush=True)
                procs = []
                abs_result_dir = os.path.abspath(context.result_dir)
                # Get the script path relative to the result directory for Docker volume mapping
                relative_script_path = os.path.relpath(context.script_path, context.result_dir).replace('\\', '/')
                
                for i in range(context.args.parallel):
                    # Calculate execution segment (e.g., 0:0.5, 0.5:1)
                    step = 1.0 / context.args.parallel
                    start = i * step
                    end = (i + 1) * step
                    segment = f"{start}:{end}"
                    
                    summary_file = f"summary_export_{i}.json"
                    results_file = f"test_results_{i}.json"
                    context.result_files.append(os.path.join(context.result_dir, results_file))
                    
                    # Docker command to run k6 with execution segment
                    cmd = [
                        "docker", "run", "--rm",
                        "-v", f"{abs_result_dir}:/results",
                        "grafana/k6:latest", "run",
                        "--execution-segment", segment,
                        "--out", f"json=/results/{results_file}",
                        "--summary-export", f"/results/{summary_file}",
                        f"/results/{relative_script_path}"
                    ]
                    
                    context.log_verbose(f"Starting container {i}: {' '.join(cmd)}")
                    # Start process, redirect stderr to stdout for unified monitoring
                    context.running_processes.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8'))
                
            else:
                # Local sequential execution
                cmd = [
                    'k6', 'run',
                    '--out', f"json={context.summary_path}",
                    '--summary-export', context.summary_export_path,
                    context.script_path
                ]
                log_comm("ExecutionAgent", "MasterAgent", "Launching k6 process...")
                context.result_files.append(context.summary_path)
                context.log_verbose(f"Executing: {' '.join(cmd)}")
                # Start process
                try:
                    context.running_processes.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8'))
                except FileNotFoundError:
                    print("\n[AGENT] I can't find the 'k6' tool on this system.", flush=True)
                    print("[AGENT] Please install k6 (https://k6.io/docs/get-started/installation/) and make sure it's in your system PATH.", flush=True)
                    log_comm("ExecutionAgent", "MasterAgent", "k6 binary not found.")
                    sys.exit(1)
        else:
            print("[STATUS] Dry Run: Skipping full test execution.", flush=True)
            with open(os.path.join(context.result_dir, 'sla_validation.json'), 'w') as f:
                json.dump({"status": "skipped", "reason": "dry-run"}, f, indent=2)
            log_comm("ExecutionAgent", "MasterAgent", "Dry run completed (no execution).")

class MonitoringAgent:
    def __init__(self):
        self.total_reqs = 0
        self.failed_reqs = 0
        self.error_rate_threshold = None
        self.lock = threading.Lock()
        self.last_alert_check_reqs = 0
        self.latency_window = []
        self.last_stats_print = time.time()
        self.interval_reqs = 0
        self.interval_errors = 0
        self.interval_bytes_recv = 0
        self.interval_bytes_sent = 0

    def run(self, context: PipelineContext):
        if not context.running_processes:
            return

        log_comm("MonitoringAgent", "MasterAgent", f"Monitoring {len(context.running_processes)} active process(es)...")
        print(f"[STATUS] Stage: Monitoring {len(context.running_processes)} active process(es)", flush=True)
        
        if context.inputs.get('sla') and 'http_req_failed_rate_max' in context.inputs['sla']:
            self.error_rate_threshold = context.inputs['sla']['http_req_failed_rate_max']

        def stream_reader(proc, prefix):
            # Read line by line from the process stdout
            for line in iter(proc.stdout.readline, ''):
                if not line: break
                clean_line = line.strip()
                if clean_line:
                    print(f"[{prefix}] {clean_line}", flush=True)
            proc.stdout.close()

        threads = []
        for i, proc in enumerate(context.running_processes):
            prefix = f"p{i}" if len(context.running_processes) > 1 else "k6"
            t = threading.Thread(target=stream_reader, args=(proc, prefix))
            t.start()
            threads.append(t)

        # Start metric tailing threads
        stop_metrics = threading.Event()
        metric_threads = []
        for fpath in context.result_files:
            t = threading.Thread(target=self.tail_metrics, args=(fpath, stop_metrics))
            t.start()
            metric_threads.append(t)

        # Wait for all streaming threads to finish (which means processes closed stdout)
        for t in threads:
            t.join()
        
        # Stop metric tailing
        stop_metrics.set()
        for t in metric_threads:
            t.join()

        # Check return codes
        for proc in context.running_processes:
            proc.wait()
            if proc.returncode != 0:
                print(f"[ERROR] Process failed with code {proc.returncode}", flush=True)
                log_comm("MonitoringAgent", "MasterAgent", f"Process failed with code {proc.returncode}")
                sys.exit(1)

        # If parallel, merge results now that execution is complete
        if context.args.parallel and context.args.parallel > 1:
            # We can reuse the logic from ExecutionAgent if we move it, or just instantiate it here.
            # For simplicity, I'll call the method on a temporary ExecutionAgent instance or move the method to static.
            # Let's use the method we moved/copied to ExecutionAgent in previous steps, but since I removed it from ExecutionAgent in this diff,
            # I will add the merge_summaries method to this class or make it a standalone function.
            # I will add it to this class for cohesion.
            log_comm("MonitoringAgent", "MasterAgent", "Merging parallel results...")
            self.merge_summaries(context.result_dir, context.args.parallel, context.summary_export_path)

    def tail_metrics(self, file_path, stop_event):
        # Wait for file to be created
        while not os.path.exists(file_path) and not stop_event.is_set():
            time.sleep(0.1)
        
        if not os.path.exists(file_path): return

        with open(file_path, 'r', encoding='utf-8') as f:
            while not stop_event.is_set():
                line = f.readline()
                if line:
                    try:
                        data = json.loads(line)
                        metric = data.get('metric')
                        value = data.get('data', {}).get('value')

                        if data.get('type') == 'Point' and metric in ['http_req_duration', 'http_reqs', 'vus', 'http_req_failed', 'data_received', 'data_sent']:
                            print(f"[METRIC] {json.dumps(data)}", flush=True)

                            if metric in ['http_reqs', 'http_req_failed', 'data_received', 'data_sent']:
                                with self.lock:
                                    if metric == 'http_reqs':
                                        self.total_reqs += 1
                                    elif metric == 'http_req_failed' and value == 1:
                                        self.failed_reqs += 1
                                    
                                    # Check error rate threshold
                                    if self.error_rate_threshold is not None and self.total_reqs > 20:
                                        # Check every 10 requests to avoid spamming
                                        if self.total_reqs > self.last_alert_check_reqs + 10:
                                            self.last_alert_check_reqs = self.total_reqs
                                            current_rate = self.failed_reqs / self.total_reqs
                                            if current_rate > self.error_rate_threshold:
                                                print(f"[CRITICAL] High error rate detected! Current: {current_rate:.2%}, Threshold: {self.error_rate_threshold:.2%}", flush=True)
                                    
                                    # Interval stats accumulators
                                    if metric == 'http_reqs':
                                        self.interval_reqs += 1
                                    elif metric == 'http_req_failed' and value > 0:
                                        self.interval_errors += 1
                                    elif metric == 'data_received':
                                        self.interval_bytes_recv += value
                                    elif metric == 'data_sent':
                                        self.interval_bytes_sent += value
                            
                            if metric == 'http_req_duration':
                                with self.lock:
                                    self.latency_window.append(value)
                                    # Keep window manageable
                                    if len(self.latency_window) > 1000:
                                        self.latency_window.pop(0)

                            # Periodic Stats Print (every 3 seconds)
                            now = time.time()
                            if now - self.last_stats_print > 3:
                                with self.lock:
                                    duration = now - self.last_stats_print
                                    self.last_stats_print = now
                                    
                                    # Latency Stats
                                    count = len(self.latency_window)
                                    avg = 0
                                    median = 0
                                    p90 = 0
                                    p95 = 0
                                    p99 = 0
                                    mn = 0
                                    mx = 0
                                    std_dev = 0
                                    
                                    if count > 0:
                                        sorted_lat = sorted(self.latency_window)
                                        avg = sum(sorted_lat) / count
                                        median = sorted_lat[int(count * 0.5)]
                                        p90 = sorted_lat[int(count * 0.90)]
                                        p95 = sorted_lat[int(count * 0.95)]
                                        p99 = sorted_lat[int(count * 0.99)]
                                        mn = sorted_lat[0]
                                        mx = sorted_lat[-1]
                                        variance = sum([((x - avg) ** 2) for x in sorted_lat]) / count
                                        std_dev = math.sqrt(variance)
                                    
                                    # Throughput & Network
                                    rps = self.interval_reqs / duration
                                    err_pct = (self.interval_errors / self.interval_reqs * 100) if self.interval_reqs > 0 else 0.0
                                    kb_recv = (self.interval_bytes_recv / 1024) / duration
                                    kb_sent = (self.interval_bytes_sent / 1024) / duration
                                    
                                    # Reset interval counters
                                    self.interval_reqs = 0; self.interval_errors = 0; self.interval_bytes_recv = 0; self.interval_bytes_sent = 0

                                    print(f"[STATS] Samples: {self.total_reqs} | RPS: {rps:.1f} | Err: {err_pct:.1f}% | Lat(ms) [Avg:{avg:.0f} Med:{median:.0f} 90%:{p90:.0f} 95%:{p95:.0f} 99%:{p99:.0f} Min:{mn:.0f} Max:{mx:.0f} Sd:{std_dev:.0f}] | Net(KB/s) [In:{kb_recv:.1f} Out:{kb_sent:.1f}]", flush=True)

                    except:
                        pass
                else:
                    time.sleep(0.1)
            # Read remaining lines after stop
            for line in f:
                try:
                    data = json.loads(line)
                    if data.get('type') == 'Point' and data.get('metric') in ['http_req_duration', 'http_reqs', 'vus', 'http_req_failed', 'data_received', 'data_sent']:
                        print(f"[METRIC] {json.dumps(data)}", flush=True)
                except:
                    pass

    def merge_summaries(self, result_dir, count, output_path):
        merged = None
        for i in range(count):
            path = os.path.join(result_dir, f"summary_export_{i}.json")
            if not os.path.exists(path): continue
            
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if merged is None:
                merged = data
                continue
            
            if 'metrics' in data:
                if 'http_req_duration' in data['metrics']:
                    m_val = merged['metrics']['http_req_duration']['values']
                    d_val = data['metrics']['http_req_duration']['values']
                    m_val['p(95)'] = max(m_val.get('p(95)', 0), d_val.get('p(95)', 0))
                
                if 'http_req_failed' in data['metrics']:
                    m_val = merged['metrics']['http_req_failed']['values']
                    d_val = data['metrics']['http_req_failed']['values']
                    m_val['passes'] += d_val.get('passes', 0)
                    m_val['fails'] += d_val.get('fails', 0)
                    total = m_val['passes'] + m_val['fails']
                    m_val['rate'] = m_val['passes'] / total if total > 0 else 0
                
                if 'http_reqs' in data['metrics']:
                    m_val = merged['metrics']['http_reqs']['values']
                    d_val = data['metrics']['http_reqs']['values']
                    m_val['count'] += d_val.get('count', 0)
                    m_val['rate'] += d_val.get('rate', 0)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(merged, f, indent=2)

class AnalysisAgent:
    def run(self, context: PipelineContext):
        log_comm("AnalysisAgent", "MasterAgent", "Analyzing results against SLA...")
        if not context.args.dry_run:
            print("[STATUS] Stage: SLA Evaluation", flush=True)
            metrics = {}
            try:
                with open(context.summary_export_path, 'r') as f:
                    metrics = json.load(f)
            except Exception as e:
                print("\n[AGENT] I couldn't read the test results.", flush=True)
                print(f"[AGENT] This might mean the test crashed or didn't produce valid JSON output. Error: {e}", flush=True)
                log_comm("AnalysisAgent", "MasterAgent", "Failed to read results.")

            context.log_verbose(f"Evaluating SLA against metrics: {list(metrics.get('metrics', {}).keys())}")
            context.sla_results = evaluate_sla(metrics, context.inputs['sla'])
            context.log_verbose(f"SLA Results: {json.dumps(context.sla_results, indent=2)}")
            with open(os.path.join(context.result_dir, 'sla_validation.json'), 'w') as f:
                json.dump(context.sla_results, f, indent=2)

            # Generate PDF Report
            print("[STATUS] Stage: PDF Report Generation", flush=True)
            pdf_path = os.path.join(context.result_dir, 'report.pdf')
            report_data = {
                'timestamp': context.timestamp,
                'input_source': context.args.file or context.args.jira_key,
                'sla_pass': context.sla_results['pass'],
                'sla_metrics': context.sla_results['verdicts'],
                'json_results_path': context.summary_path
            }
            try:
                generate_pdf_report(pdf_path, report_data)
            except Exception as e:
                print(f"[WARN] Failed to generate PDF report: {e}", flush=True)

        log_comm("AnalysisAgent", "MasterAgent", "Generating Final Summary...")
        # AI-Driven Analysis
        ai_insights = ""
        if genai and not context.args.dry_run:
            api_key = os.environ.get('GEMINI_API_KEY') or context.config.get('gemini_api_key')
            if api_key:
                print("[STATUS] Generating AI Performance Insights...", flush=True)
                try:
                    client = genai.Client(api_key=api_key)
                    
                    # Prepare context for AI
                    metrics_summary = {}
                    if metrics and 'metrics' in metrics:
                        for k in ['http_req_duration', 'http_reqs', 'http_req_failed']:
                            if k in metrics['metrics']:
                                metrics_summary[k] = metrics['metrics'][k]['values']

                    prompt = f"""
                    Analyze these load test results and provide a brief performance assessment.
                    
                    SLA Verdicts: {json.dumps(context.sla_results, indent=2)}
                    Key Metrics: {json.dumps(metrics_summary, indent=2)}
                    
                    Provide:
                    1. A 1-sentence executive summary.
                    2. Top 3 observations/recommendations.
                    """
                    response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
                    print(f"[INFO] AI Insights Generated:\n{response.text}", flush=True)
                    ai_insights = f"\n## 🤖 AI Insights\n{response.text}\n"
                except Exception as e:
                    print(f"[WARN] AI Analysis failed: {e}", flush=True)

        # Final Summary
        sla_verdict_str = str(context.sla_results['pass']) if not context.args.dry_run else "N/A (Dry Run)"
        context.summary_md = f"""
# Test Summary

**Date**: {context.timestamp}
**Input**: {context.args.file or context.args.jira_key}

## Validation
- Script Lint: PASS
- Smoke Run: PASS

## SLA Verdict
**Overall Pass**: {sla_verdict_str}

### Metrics
```json
{json.dumps(context.sla_results['verdicts'], indent=2)}
```

## Artifacts
- Script
- Results
- PDF Report
"""
        with open(os.path.join(context.result_dir, 'summary.md'), 'w') as f:
            f.write(context.summary_md)

        print("[STATUS] Pipeline Complete.", flush=True)
        print(f"Artifacts saved in: {context.result_dir}", flush=True)
        if not context.args.dry_run:
            if not context.sla_results['pass']:
                print("[WARN] SLA Failed.", flush=True)
            else:
                print("[SUCCESS] SLA Passed.", flush=True)
            log_comm("AnalysisAgent", "MasterAgent", f"SLA Verdict: {'PASS' if context.sla_results['pass'] else 'FAIL'}")
        else:
            print("[SUCCESS] Dry Run Complete.", flush=True)

class NotificationAgent:
    def run(self, context: PipelineContext):
        log_comm("NotificationAgent", "MasterAgent", "Checking notification settings...")
        if context.args.notify:
            print(f"[STATUS] Sending notification to {context.args.notify}", flush=True)
            try:
                # Determine message urgency based on SLA
                if context.sla_results and not context.sla_results.get('pass', True):
                    message_text = f"🚨 **CRITICAL: SLA FAILED** 🚨\n\n{context.summary_md}"
                else:
                    message_text = f"✅ **SLA PASSED**\n\n{context.summary_md}"

                sent = False
                # Try sending with PDF attachment if exists
                pdf_path = os.path.join(context.result_dir, 'report.pdf')
                if os.path.exists(pdf_path):
                    try:
                        with open(pdf_path, 'rb') as f:
                            files = {'file': ('report.pdf', f, 'application/pdf')}
                            data = {'text': message_text, 'payload_json': json.dumps({'content': message_text})}
                            resp = requests.post(context.args.notify, files=files, data=data, timeout=30)
                            if 200 <= resp.status_code < 300:
                                print("[SUCCESS] Notification sent with PDF attachment.", flush=True)
                                sent = True
                    except Exception as e:
                        print(f"[WARN] Failed to send attachment: {e}", flush=True)

                # Fallback to text-only
                if not sent:
                    payload = {"text": message_text}
                    resp = requests.post(context.args.notify, json=payload, timeout=10)
                    if 200 <= resp.status_code < 300:
                        print("[SUCCESS] Notification sent (text only).", flush=True)
                    else:
                        print(f"[WARN] Notification failed: {resp.status_code} {resp.text}", flush=True)
            except Exception as e:
                print(f"[WARN] Notification failed: {e}", flush=True)
            log_comm("NotificationAgent", "MasterAgent", "Notification attempt finished.")

class CleanupAgent:
    def run(self, context: PipelineContext):
        log_comm("CleanupAgent", "MasterAgent", "Checking disk usage...")
        print("[STATUS] Stage: Cleanup Check", flush=True)
        threshold = context.args.cleanup_threshold
        output_dir = context.args.output_dir
        
        if not os.path.exists(output_dir):
            return

        total, used, free = shutil.disk_usage(output_dir)
        percent_used = (used / total) * 100
        
        if percent_used > threshold:
            print(f"[WARN] Disk usage {percent_used:.2f}% exceeds threshold {threshold}%. Archiving old results...", flush=True)
            
            # List subdirs, sort by modification time (oldest first)
            subdirs = [os.path.join(output_dir, d) for d in os.listdir(output_dir) if os.path.isdir(os.path.join(output_dir, d))]
            subdirs.sort(key=lambda x: os.path.getmtime(x))
            
            for d in subdirs:
                if os.path.abspath(d) == os.path.abspath(context.result_dir):
                    continue # Skip current run
                
                shutil.make_archive(d, 'zip', d)
                shutil.rmtree(d)
                print(f"[INFO] Archived and deleted {d}", flush=True)
                
                # Check usage again
                _, used, _ = shutil.disk_usage(output_dir)
                if (used / total) * 100 <= threshold:
                    break

class MasterAgent:
    def orchestrate(self, context, agents):
        log_comm("MasterAgent", "ALL", "Initializing Multi-Agent Pipeline...")
        
        for agent in agents:
            name = agent.__class__.__name__
            log_comm("MasterAgent", name, f"Activating {name}")
            
            try:
                agent.run(context)
                log_comm(name, "MasterAgent", "Task completed successfully.")
            except SystemExit as e:
                log_comm(name, "MasterAgent", f"Agent halted pipeline with code {e.code}")
                raise e
            except Exception as e:
                log_comm(name, "MasterAgent", f"CRITICAL FAILURE: {str(e)}")
                raise e
        log_comm("MasterAgent", "ALL", "Pipeline execution finished.")

def main():
    print("[STATUS] Agent initialized and starting...", flush=True)
    parser = argparse.ArgumentParser(description="Performance Test Agent")
    parser.add_argument('--jira_key', type=str)
    parser.add_argument('--jira_url', type=str)
    parser.add_argument('--jira_auth', type=str)
    parser.add_argument('--file', type=str)
    parser.add_argument('--output_dir', type=str, default=None)
    parser.add_argument('--dry-run', action='store_true', help="Skip actual test execution")
    parser.add_argument('--verbose', action='store_true', help="Enable verbose logging")
    parser.add_argument('--clean', action='store_true', help="Clean results directory before running")
    parser.add_argument('--config', type=str, default='config.yaml', help="Path to configuration file")
    parser.add_argument('--profile', type=str, help="Configuration profile (loads PROFILE.yaml)")
    parser.add_argument('--compare', nargs=2, help="Compare two result folders", metavar=('FOLDER1', 'FOLDER2'))
    parser.add_argument('--notify', type=str, help="Webhook URL to send test summary")
    parser.add_argument('--parallel', type=int, default=1, help="Number of parallel Docker containers")
    parser.add_argument('--cleanup-threshold', type=int, default=90, help="Disk usage percentage threshold for cleanup")
    
    args = parser.parse_args()

    # Handle compare mode
    if args.compare:
        compare_results(args.compare[0], args.compare[1])
        return

    # Initialize Pipeline Context
    context = PipelineContext(args)
    
    # Define Agents
    agents = [
        IngestionAgent(),
        GeneratorAgent(),
        ValidationAgent(),
        ExecutionAgent(),
        MonitoringAgent(),
        AnalysisAgent(),
        NotificationAgent(),
        CleanupAgent()
    ]
    
    # Orchestrate
    master = MasterAgent()
    master.orchestrate(context, agents)

if __name__ == "__main__":
    main()