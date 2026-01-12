from flask import Flask, request, Response, jsonify, send_from_directory, send_file
import subprocess
import os
import json
import time
import sys
import io
import zipfile
import yaml
import shutil
import psutil
import platform
import socket
import webbrowser
import threading
from threading import Timer
from datetime import timedelta
try:
    from google import genai
except ImportError:
    genai = None
    print("[WARN] 'google-genai' library not found. Chat features will be limited.")

app = Flask(__name__)

current_process = None
server_state = {'is_running': False}
log_history = []
START_TIME = time.time()

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/results/<path:filename>')
def results(filename):
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    return send_from_directory(results_dir, filename)

def process_monitor(proc, temp_file):
    global server_state, log_history
    
    # Read output
    for line in iter(proc.stdout.readline, ''):
        if not line: break
        msg = {'type': 'stdout', 'message': line.rstrip()}
        log_history.append(msg)
        
    proc.wait()
    
    # Cleanup
    if temp_file and os.path.exists(temp_file):
        os.remove(temp_file)
        
    done_msg = {'type': 'done', 'code': {'code': proc.returncode}}
    log_history.append(done_msg)
    server_state['is_running'] = False

@app.route('/run', methods=['POST'])
def run():
    global current_process
    global server_state
    global log_history
    
    if server_state['is_running']:
        return jsonify({'error': 'Test already running'}), 409

    data = request.json
    mode = data.get('mode')
    jira = data.get('jira', {})
    file_content = data.get('fileContent')
    dry_run = data.get('dryRun', False)
    verbose = data.get('verbose', False)
    clean = data.get('clean', False)
    config_profile = data.get('configProfile')
    webhook_url = data.get('webhookUrl')
    cleanup_threshold = data.get('cleanupThreshold')
    parallel = data.get('parallel')

    # Reset history
    log_history = []

    # Use the current python executable to run the agent
    cmd = [sys.executable, '-u', 'agent.py'] # -u for unbuffered output
    temp_file = None

    if mode == 'jira':
        cmd.extend([
            '--jira_url', jira.get('base_url', ''),
            '--jira_key', jira.get('issue_key', ''),
            '--jira_auth', jira.get('auth', '')
        ])
    else:
        temp_file = f"temp_input_{int(time.time())}.json"
        # Write temp file
        with open(temp_file, 'w', encoding='utf-8') as f:
            if isinstance(file_content, str):
                f.write(file_content)
            else:
                json.dump(file_content, f, indent=2)
        cmd.extend(['--file', temp_file])

    if dry_run:
        cmd.append('--dry-run')

    if verbose:
        cmd.append('--verbose')

    if clean:
        cmd.append('--clean')

    if config_profile:
        cmd.extend(['--config', config_profile])

    if webhook_url:
        cmd.extend(['--notify', webhook_url])

    if cleanup_threshold:
        cmd.extend(['--cleanup-threshold', str(cleanup_threshold)])

    if parallel:
        cmd.extend(['--parallel', str(parallel)])

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    # Run agent as subprocess
    # Merge stderr into stdout to simplify streaming
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        bufsize=1, # Line buffered
        universal_newlines=True
    )
    current_process = proc
    server_state['is_running'] = True

    # Start monitor thread
    t = threading.Thread(target=process_monitor, args=(proc, temp_file))
    t.daemon = True
    t.start()

    return Response(stream_logs(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no' # Disable Nginx buffering if present
    })

def stream_logs():
    curr = 0
    while True:
        if curr < len(log_history):
            yield f"data: {json.dumps(log_history[curr])}\n\n"
            curr += 1
        elif not server_state['is_running']:
            # Check one last time for any remaining logs after process finished
            if curr < len(log_history):
                continue
            break
        else:
            time.sleep(0.1)

@app.route('/stream', methods=['GET'])
def stream_endpoint():
    return Response(stream_logs(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no'
    })

@app.route('/stop', methods=['POST'])
def stop():
    global current_process
    global server_state
    if current_process and current_process.poll() is None:
        current_process.terminate()
        server_state['is_running'] = False
        return jsonify({'status': 'success', 'message': 'Pipeline execution stopped.'})
    return jsonify({'status': 'error', 'message': 'No running pipeline found.'})

@app.route('/api/status', methods=['GET'])
def get_status():
    global server_state
    return jsonify(server_state)

@app.route('/compare', methods=['POST'])
def compare():
    data = request.json
    folder1 = data.get('folder1')
    folder2 = data.get('folder2')
    
    if not folder1 or not folder2:
        return jsonify({'error': 'Two folders required'}), 400

    def generate():
        cmd = [sys.executable, '-u', 'agent.py', '--compare', folder1, folder2]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            bufsize=1,
            universal_newlines=True
        )
        
        for line in proc.stdout:
            yield f"data: {json.dumps({'type': 'stdout', 'message': line.rstrip()})}\n\n"
            
        proc.wait()
        yield f"data: {json.dumps({'type': 'done', 'code': {'code': proc.returncode}})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/history')
def history():
    res_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    if not os.path.exists(res_dir):
        return jsonify([])
    
    try:
        # List directories, sort by name desc (timestamp)
        items = []
        for d in os.listdir(res_dir):
            full_path = os.path.join(res_dir, d)
            if os.path.isdir(full_path):
                enhanced = os.path.exists(os.path.join(full_path, 'neuro_san.flag'))
                
                # Determine Pass/Fail status
                status = 'UNKNOWN'
                sla_path = os.path.join(full_path, 'sla_validation.json')
                
                if os.path.exists(os.path.join(full_path, 'preflight_failed.flag')):
                    status = 'PRE-FLIGHT FAILED'
                
                has_extracted = os.path.exists(os.path.join(full_path, 'extracted_files.json'))
                
                if os.path.exists(sla_path):
                    try:
                        with open(sla_path, 'r') as f:
                            sla_data = json.load(f)
                            status = 'PASS' if sla_data.get('pass') else 'FAIL'
                    except: pass
                
                items.append({'name': d, 'enhanced': enhanced, 'status': status, 'has_extracted': has_extracted})
        
        items.sort(key=lambda x: x['name'], reverse=True)
        return jsonify(items)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reanalyze', methods=['POST'])
def reanalyze():
    data = request.json
    folder = data.get('folder')
    if not folder:
        return jsonify({'error': 'Folder required'}), 400
    
    # Security check
    if '..' in folder or folder.startswith('/') or folder.startswith('\\'):
         return jsonify({'error': 'Invalid folder'}), 400

    target_dir = os.path.join('results', folder)
    if not os.path.exists(target_dir):
        return jsonify({'error': 'Folder not found'}), 404

    def generate():
        cmd = [sys.executable, '-u', 'agent.py', '--reanalyze', target_dir]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            bufsize=1,
            universal_newlines=True
        )
        
        for line in proc.stdout:
            yield f"data: {json.dumps({'type': 'stdout', 'message': line.rstrip()})}\n\n"
            
        proc.wait()
        yield f"data: {json.dumps({'type': 'done', 'code': {'code': proc.returncode}})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/delete-run', methods=['POST'])
def delete_run():
    data = request.json
    folders = data.get('folders', [])
    if not folders:
        return jsonify({'error': 'No folders specified'}), 400
    
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    deleted = []
    
    for folder in folders:
        # Basic security check
        if '..' in folder or '/' in folder or '\\' in folder:
            continue
        target = os.path.join(base_dir, folder)
        if os.path.exists(target):
            shutil.rmtree(target)
            deleted.append(folder)
            
    return jsonify({'deleted': deleted})

@app.route('/api/delete-extracted-file', methods=['POST'])
def delete_extracted_file():
    data = request.json
    folder = data.get('folder')
    filename = data.get('filename')
    
    if not folder or not filename:
        return jsonify({'error': 'Folder and filename required'}), 400
    
    # Security check
    if '..' in folder or folder.startswith('/') or folder.startswith('\\'):
         return jsonify({'error': 'Invalid folder'}), 400
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
         return jsonify({'error': 'Invalid filename'}), 400

    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_path = os.path.join(base_dir, 'results', folder, 'scripts', filename)
    
    try:
        if os.path.exists(target_path):
            if os.path.isdir(target_path):
                 shutil.rmtree(target_path)
            else:
                 os.remove(target_path)
        
        # Update extracted_files.json
        json_path = os.path.join(base_dir, 'results', folder, 'extracted_files.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    files = json.load(f)
                
                if filename in files:
                    files.remove(filename)
                    with open(json_path, 'w') as f:
                        json.dump(files, f)
            except Exception as e:
                print(f"Error updating extracted_files.json: {e}")
                
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload-extracted-file', methods=['POST'])
def upload_extracted_file():
    folder = request.form.get('folder')
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    
    if not folder or not file or file.filename == '':
        return jsonify({'error': 'Folder and file required'}), 400
    
    # Security check
    if '..' in folder or folder.startswith('/') or folder.startswith('\\'):
         return jsonify({'error': 'Invalid folder'}), 400
    
    filename = file.filename
    # Basic filename sanitization
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
         return jsonify({'error': 'Invalid filename'}), 400

    base_dir = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.join(base_dir, 'results', folder, 'scripts')
    
    if not os.path.exists(scripts_dir):
        os.makedirs(scripts_dir, exist_ok=True)
        
    target_path = os.path.join(scripts_dir, filename)
    try:
        file.save(target_path)
        
        # Update extracted_files.json
        json_path = os.path.join(base_dir, 'results', folder, 'extracted_files.json')
        files = []
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    files = json.load(f)
            except:
                pass
        
        if filename not in files:
            files.append(filename)
            with open(json_path, 'w') as f:
                json.dump(files, f)
                
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/create-extracted-file', methods=['POST'])
def create_extracted_file():
    data = request.json
    folder = data.get('folder')
    filename = data.get('filename')
    
    if not folder or not filename:
        return jsonify({'error': 'Folder and filename required'}), 400
    
    # Security check
    if '..' in folder or folder.startswith('/') or folder.startswith('\\'):
         return jsonify({'error': 'Invalid folder'}), 400
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
         return jsonify({'error': 'Invalid filename'}), 400

    base_dir = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.join(base_dir, 'results', folder, 'scripts')
    
    if not os.path.exists(scripts_dir):
        os.makedirs(scripts_dir, exist_ok=True)
        
    target_path = os.path.join(scripts_dir, filename)
    
    try:
        if os.path.exists(target_path):
            return jsonify({'error': 'File already exists'}), 409
            
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, 'w') as f:
            pass # Create empty file
        
        # Update extracted_files.json
        json_path = os.path.join(base_dir, 'results', folder, 'extracted_files.json')
        files = []
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    files = json.load(f)
            except:
                pass
        
        if filename not in files:
            files.append(filename)
            with open(json_path, 'w') as f:
                json.dump(files, f)
                
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/save-extracted-file', methods=['POST'])
def save_extracted_file():
    data = request.json
    folder = data.get('folder')
    filename = data.get('filename')
    content = data.get('content', '')
    
    if not folder or not filename:
        return jsonify({'error': 'Folder and filename required'}), 400
    
    # Security check
    if '..' in folder or folder.startswith('/') or folder.startswith('\\'):
         return jsonify({'error': 'Invalid folder'}), 400
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
         return jsonify({'error': 'Invalid filename'}), 400

    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_path = os.path.join(base_dir, 'results', folder, 'scripts', filename)
    
    try:
        if not os.path.exists(target_path):
            return jsonify({'error': 'File not found'}), 404
            
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
                
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/rename-extracted-file', methods=['POST'])
def rename_extracted_file():
    data = request.json
    folder = data.get('folder')
    old_filename = data.get('old_filename')
    new_filename = data.get('new_filename')
    
    if not folder or not old_filename or not new_filename:
        return jsonify({'error': 'Folder, old filename, and new filename required'}), 400
    
    # Security check
    for name in [folder, old_filename, new_filename]:
        if '..' in name or name.startswith('/') or name.startswith('\\'):
             return jsonify({'error': 'Invalid path components'}), 400

    base_dir = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.join(base_dir, 'results', folder, 'scripts')
    old_path = os.path.join(scripts_dir, old_filename)
    new_path = os.path.join(scripts_dir, new_filename)
    
    try:
        if not os.path.exists(old_path):
            return jsonify({'error': 'File not found'}), 404
        
        if os.path.exists(new_path):
            return jsonify({'error': 'Destination file already exists'}), 409
            
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.rename(old_path, new_path)
        
        # Update extracted_files.json
        json_path = os.path.join(base_dir, 'results', folder, 'extracted_files.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    files = json.load(f)
                
                if old_filename in files:
                    index = files.index(old_filename)
                    files[index] = new_filename
                    with open(json_path, 'w') as f:
                        json.dump(files, f)
            except Exception as e:
                print(f"Error updating extracted_files.json: {e}")
                
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/duplicate-extracted-file', methods=['POST'])
def duplicate_extracted_file():
    data = request.json
    folder = data.get('folder')
    filename = data.get('filename')
    new_filename = data.get('new_filename')
    
    if not folder or not filename or not new_filename:
        return jsonify({'error': 'Folder, filename, and new filename required'}), 400
    
    # Security check
    for name in [folder, filename, new_filename]:
        if '..' in name or name.startswith('/') or name.startswith('\\'):
             return jsonify({'error': 'Invalid path components'}), 400

    base_dir = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.join(base_dir, 'results', folder, 'scripts')
    src_path = os.path.join(scripts_dir, filename)
    dst_path = os.path.join(scripts_dir, new_filename)
    
    try:
        if not os.path.exists(src_path):
            return jsonify({'error': 'Source file not found'}), 404
        
        if os.path.exists(dst_path):
            return jsonify({'error': 'Destination file already exists'}), 409
            
        shutil.copy2(src_path, dst_path)
        
        # Update extracted_files.json
        json_path = os.path.join(base_dir, 'results', folder, 'extracted_files.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    files = json.load(f)
                
                if new_filename not in files:
                    files.append(new_filename)
                    with open(json_path, 'w') as f:
                        json.dump(files, f)
            except Exception as e:
                print(f"Error updating extracted_files.json: {e}")
                
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profiles', methods=['GET'])
def list_profiles():
    try:
        # List all yaml files in current directory
        files = [f for f in os.listdir('.') if os.path.isfile(f) and (f.endswith('.yaml') or f.endswith('.yml'))]
        files.sort()
        return jsonify(files)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download/<path:foldername>')
def download_folder(foldername):
    res_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results', foldername)
    if not os.path.exists(res_dir):
        return jsonify({'error': 'Folder not found'}), 404
        
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(res_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, res_dir)
                zf.write(file_path, arcname)
    
    memory_file.seek(0)
    return send_file(memory_file, download_name=f"{foldername}.zip", as_attachment=True)

@app.route('/save-config', methods=['POST'])
def save_config():
    data = request.json
    config = {}
    
    # Load existing config to preserve other values
    if os.path.exists('config.yaml'):
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
        except:
            pass
    
    if data.get('jira'):
        jira = data['jira']
        if jira.get('base_url'): config['jira_url'] = jira['base_url']
        if jira.get('issue_key'): config['jira_key'] = jira['issue_key']
        if jira.get('auth'): config['jira_auth'] = jira['auth']
    
    if data.get('gemini_api_key'):
        config['gemini_api_key'] = data['gemini_api_key']

    if 'neuro_san_auto_update' in data:
        config['neuro_san_auto_update'] = data['neuro_san_auto_update']

    if 'neuro_san_script' in data:
        config['neuro_san_script'] = data['neuro_san_script']

    try:
        with open('config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False)
        return jsonify({'status': 'success', 'message': 'Configuration saved to config.yaml'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get-config', methods=['GET'])
def get_config():
    if not os.path.exists('config.yaml'):
        return jsonify({})
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        return jsonify(config)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reset-config', methods=['POST'])
def reset_config():
    try:
        if os.path.exists('config.yaml'):
            os.remove('config.yaml')
            return jsonify({'status': 'success', 'message': 'Configuration reset (file deleted).'})
        else:
            return jsonify({'status': 'success', 'message': 'No configuration file found to reset.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get-config-raw', methods=['GET'])
def get_config_raw():
    filename = request.args.get('file', 'config.yaml')
    # Basic security check
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
        return jsonify({'error': 'Invalid filename'}), 400
    
    if not os.path.exists(filename):
        return jsonify({'content': ''})
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return jsonify({'content': f.read()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/save-config-raw', methods=['POST'])
def save_config_raw():
    data = request.json
    filename = data.get('filename', 'config.yaml')
    content = data.get('content', '')
    
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
        return jsonify({'error': 'Invalid filename'}), 400

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'status': 'success', 'message': f'Configuration saved to {filename}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/env', methods=['GET'])
def get_env_vars():
    safe_env = {}
    for k, v in os.environ.items():
        # Mask sensitive variables
        if any(s in k.upper() for s in ['TOKEN', 'KEY', 'PASS', 'AUTH', 'SECRET']):
            safe_env[k] = '****'
        else:
            safe_env[k] = v
    return jsonify(safe_env)

@app.route('/api/system-health')
def system_health():
    health = {
        'cpu_percent': psutil.cpu_percent(interval=0.1),
        'memory_percent': psutil.virtual_memory().percent,
        'uptime': str(timedelta(seconds=int(time.time() - START_TIME))),
        'hostname': socket.gethostname(),
        'os_info': f"{platform.system()} {platform.release()}"
    }
    
    return jsonify(health)

@app.route('/api/architecture', methods=['GET'])
def get_architecture():
    if not os.path.exists('ARCHITECTURE.md'):
        return jsonify({'error': 'Architecture file not found'}), 404
    try:
        with open('ARCHITECTURE.md', 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'content': content})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message')
    context_logs = data.get('context', '')
    
    if genai is None:
        response_text = "I'm currently in basic mode because the 'google-genai' library is not installed. Please run `pip install google-genai` to enable intelligent assistance.\n\n"
        if message and ("error" in message.lower() or "fail" in message.lower()):
             response_text += "It looks like you're encountering an error. Please check the Console tab for detailed diagnostics."
        else:
             response_text += "I can help you run tests if you provide a valid configuration."
        return jsonify({'response': response_text})
    
    api_key = os.environ.get('GEMINI_API_KEY')
    if api_key:
        api_key = api_key.strip()

    if not api_key:
        # Try loading from config.yaml
        if os.path.exists('config.yaml'):
            try:
                with open('config.yaml', 'r', encoding='utf-8') as f:
                    conf = yaml.safe_load(f) or {}
                    api_key = conf.get('gemini_api_key')
                    if api_key:
                        api_key = api_key.strip()
            except:
                pass
    
    if not api_key:
        return jsonify({'response': "I'm sorry, but I can't provide intelligent assistance right now because the GEMINI_API_KEY is not set. Please set it in the configuration panel or as an environment variable."})

    try:
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
        You are a helpful AI assistant for a Performance Test Agent tool. 
        The user is asking for help. Here is the recent log context from the tool:
        
        {context_logs}
        
        User Question: {message}
        
        Please provide a concise and helpful answer.
        """
        response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
        return jsonify({'response': response.text})
    except Exception as e:
        return jsonify({'response': f"I encountered an error while trying to think: {str(e)}"})

def open_browser():
    webbrowser.open_new_tab("http://127.0.0.1:3000")

if __name__ == '__main__':
    print("Starting server at http://localhost:3000")
    if genai:
        print("[INFO] Chat features enabled (google-genai found).")
    else:
        print("[INFO] Chat features limited (google-genai not found).")
    Timer(1, open_browser).start()
    app.run(host='0.0.0.0', port=3000)