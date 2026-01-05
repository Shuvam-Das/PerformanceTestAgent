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

app = Flask(__name__)

current_process = None

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/results/<path:filename>')
def results(filename):
    return send_from_directory('results', filename)

@app.route('/run', methods=['POST'])
def run():
    global current_process
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

    def generate():
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

        # Run agent as subprocess
        # Merge stderr into stdout to simplify streaming
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1, # Line buffered
            universal_newlines=True
        )
        current_process = proc

        # Stream output
        for line in proc.stdout:
            yield f"data: {json.dumps({'type': 'stdout', 'message': line.rstrip()})}\n\n"
        
        proc.wait()
        current_process = None

        # Cleanup
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)

        yield f"data: {json.dumps({'type': 'done', 'code': {'code': proc.returncode}})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/stop', methods=['POST'])
def stop():
    global current_process
    if current_process and current_process.poll() is None:
        current_process.terminate()
        return jsonify({'status': 'success', 'message': 'Pipeline execution stopped.'})
    return jsonify({'status': 'error', 'message': 'No running pipeline found.'})

@app.route('/compare', methods=['POST'])
def compare():
    data = request.json
    folder1 = data.get('folder1')
    folder2 = data.get('folder2')
    
    if not folder1 or not folder2:
        return jsonify({'error': 'Two folders required'}), 400

    def generate():
        cmd = [sys.executable, '-u', 'agent.py', '--compare', folder1, folder2]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
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
    res_dir = os.path.join(os.getcwd(), 'results')
    if not os.path.exists(res_dir):
        return jsonify([])
    
    try:
        # List directories, sort by name desc (timestamp)
        items = [d for d in os.listdir(res_dir) if os.path.isdir(os.path.join(res_dir, d))]
        items.sort(reverse=True)
        return jsonify(items)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete-run', methods=['POST'])
def delete_run():
    data = request.json
    folders = data.get('folders', [])
    if not folders:
        return jsonify({'error': 'No folders specified'}), 400
    
    base_dir = os.path.join(os.getcwd(), 'results')
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
    res_dir = os.path.join(os.getcwd(), 'results', foldername)
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
    
    if data.get('jira'):
        jira = data['jira']
        if jira.get('base_url'): config['jira_url'] = jira['base_url']
        if jira.get('issue_key'): config['jira_key'] = jira['issue_key']
        if jira.get('auth'): config['jira_auth'] = jira['auth']

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000)