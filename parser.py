import json
import yaml
import re
import requests
import shlex

def parse_input(config):
    content = config.get('content', "")
    diagnostics = []

    # 1. Input Ingestion
    if not content:
        if config.get('jira'):
            try:
                jira = config['jira']
                headers = {}
                if jira.get('auth'):
                    headers['Authorization'] = jira['auth']
                
                url = f"{jira['base_url']}/rest/api/2/issue/{jira['issue_key']}"
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                content = response.json().get('fields', {}).get('description', "")
            except Exception as e:
                diagnostics.append({"path": "$.jira", "reason": f"Failed to fetch Jira issue: {str(e)}"})
        elif config.get('file'):
            try:
                with open(config['file'], 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                diagnostics.append({"path": "$.file", "reason": f"Failed to read local file: {str(e)}"})
        else:
            diagnostics.append({"path": "$", "reason": "No input source provided (Jira or File)"})

    if diagnostics:
        return {"result": None, "diagnostics": diagnostics}

    result = {
        "api_collection": None,
        "workload_scenario": None,
        "sla": None,
        "env": None,
        "neuro_san_config": None,
        "test_data": None
    }

    # 2. Parsing Logic
    json_blocks = re.findall(r'```json([\s\S]*?)```', content)
    yaml_blocks = re.findall(r'```yaml([\s\S]*?)```', content)
    
    # Capture generic blocks and filter out those that look like json/yaml blocks (to avoid duplicates)
    plain_blocks_raw = re.findall(r'```([\s\S]*?)```', content)
    plain_blocks = []
    for block in plain_blocks_raw:
        if not block.startswith(('json', 'yaml')):
            plain_blocks.append(block)

    def try_parse(s, fmt):
        try:
            if fmt == 'yaml':
                return yaml.safe_load(s)
            return json.loads(s)
        except:
            return None

    all_blocks = json_blocks + yaml_blocks + plain_blocks
    
    # Fallback: If no blocks found, try parsing the entire content
    if not all_blocks and content.strip():
        all_blocks.append(content)

    for block in all_blocks:
        clean = block.strip()
        obj = try_parse(clean, 'json')
        if not obj:
            obj = try_parse(clean, 'yaml')
        
        if not obj:
            curl_endpoints = parse_curl(clean)
            if curl_endpoints:
                if not result['api_collection']:
                    result['api_collection'] = {'endpoints': []}
                if 'endpoints' in result['api_collection']:
                    result['api_collection']['endpoints'].extend(curl_endpoints)

        if obj:
            if isinstance(obj, dict):
                if obj.get('openapi') or obj.get('swagger'):
                    result['api_collection'] = {'openapi': obj}
                elif obj.get('info') and obj.get('item'):
                    result['api_collection'] = {
                        'postman': obj,
                        'endpoints': extract_postman_endpoints(obj.get('item', []))
                    }
                elif obj.get('api_collection', {}).get('endpoints'):
                    result['api_collection'] = {'endpoints': obj['api_collection']['endpoints']}
                elif obj.get('log') and obj['log'].get('entries'):
                    result['api_collection'] = {
                        'har': obj,
                        'endpoints': extract_har_endpoints(obj['log']['entries'])
                    }
                elif obj.get('_postman_variable_scope') == 'environment' or (obj.get('values') and isinstance(obj.get('values'), list) and obj.get('name') and not obj.get('item')):
                    # Environment
                    env_vars = {}
                    for v in obj.get('values', []):
                        if v.get('enabled') is not False and v.get('key'):
                            env_vars[v['key']] = v['value']
                    
                    if not result['env']: result['env'] = {}
                    
                    url = env_vars.get('baseUrl') or env_vars.get('base_url') or env_vars.get('host')
                    if url and not result['env'].get('base_url'):
                        result['env']['base_url'] = url
                    
                    existing_vars = result['env'].get('variables', {})
                    existing_vars.update(env_vars)
                    result['env']['variables'] = existing_vars

                if obj.get('workload_scenario'):
                    result['workload_scenario'] = obj['workload_scenario']
                elif obj.get('type') in ['constant-vus', 'ramping-vus']:
                    result['workload_scenario'] = obj
                
                if obj.get('sla'):
                    result['sla'] = obj['sla']
                elif obj.get('http_req_duration_p95_ms') or obj.get('throughput_rps_min'):
                    result['sla'] = obj
                
                if obj.get('env'):
                    result['env'] = obj['env']
                
                if obj.get('neuro_san_config'):
                    result['neuro_san_config'] = obj['neuro_san_config']
                
                if obj.get('test_data'):
                    result['test_data'] = obj['test_data']
            elif isinstance(obj, list) and len(obj) > 0 and obj[0].get('method') and obj[0].get('url'):
                # Enrich endpoints if they are simple dicts
                for ep in obj:
                    if 'extract' not in ep: ep['extract'] = {}
                    if 'assertions' not in ep: ep['assertions'] = []
                result['api_collection'] = {'endpoints': obj}

    # 3. Endpoint Normalization
    if result['api_collection'] and result['api_collection'].get('endpoints'):
        unique = {}
        for idx, ep in enumerate(result['api_collection']['endpoints']):
            if not ep.get('method') or not ep.get('url'):
                diagnostics.append({"path": f"$.api_collection.endpoints[{idx}]", "reason": "Missing method or url"})
                continue
            
            # Normalize path params
            normalized_url = re.sub(r'/\d+', '/{id}', ep['url'])
            key = f"{ep['method'].upper()}:{normalized_url}"
            if key not in unique:
                unique[key] = ep
        result['api_collection']['endpoints'] = list(unique.values())

    return {"result": result, "diagnostics": diagnostics}

def extract_har_endpoints(entries):
    endpoints = []
    if not isinstance(entries, list):
        return endpoints
        
    for entry in entries:
        req = entry.get('request')
        if not req:
            continue
            
        method = req.get('method', 'GET')
        url = req.get('url', '')
        
        headers = {}
        if isinstance(req.get('headers'), list):
            for h in req['headers']:
                if h.get('name'):
                    headers[h['name']] = h.get('value', '')
        
        body = None
        if req.get('postData') and req['postData'].get('text'):
            try:
                body = json.loads(req['postData']['text'])
            except:
                body = req['postData']['text']
        
        endpoints.append({
            "name": url,
            "method": method,
            "url": url,
            "headers": headers if headers else None,
            "body": body,
            "extract": {},
            "assertions": []
        })
    return endpoints

def extract_postman_endpoints(items):
    endpoints = []
    if not isinstance(items, list):
        return endpoints
    
    for item in items:
        if item.get('item'):
            endpoints.extend(extract_postman_endpoints(item['item']))
        elif item.get('request'):
            req = item['request']
            method = req.get('method', 'GET')
            url = ""
            
            if isinstance(req.get('url'), str):
                url = req['url']
            elif isinstance(req.get('url'), dict) and req['url'].get('raw'):
                url = req['url']['raw']
            
            headers = {}
            if isinstance(req.get('header'), list):
                for h in req['header']:
                    if h.get('key'):
                        headers[h['key']] = h['value']
            
            body = None
            if req.get('body') and req['body'].get('mode') == 'raw' and req['body'].get('raw'):
                try:
                    body = json.loads(req['body']['raw'])
                except:
                    body = req['body']['raw']
            
            endpoints.append({
                "name": item.get('name', 'Untitled'),
                "method": method,
                "url": url,
                "headers": headers if headers else None,
                "body": body,
                "extract": {},
                "assertions": []
            })
    return endpoints

def parse_curl(cmd_str):
    try:
        tokens = shlex.split(cmd_str)
    except:
        return []
    
    endpoints = []
    current_endpoint = None
    
    i = 0
    while i < len(tokens):
        token = tokens[i]
        
        if token == 'curl':
            if current_endpoint:
                endpoints.append(current_endpoint)
            current_endpoint = {
                'method': 'GET',
                'headers': {},
                'body': None,
                'url': None,
                'name': 'Curl Request',
                'extract': {},
                'assertions': []
            }
            i += 1
            continue
            
        if not current_endpoint:
            i += 1
            continue
            
        if token in ['-X', '--request']:
            if i + 1 < len(tokens):
                current_endpoint['method'] = tokens[i+1]
                i += 2
            else:
                i += 1
        elif token in ['-H', '--header']:
            if i + 1 < len(tokens):
                header_str = tokens[i+1]
                if ':' in header_str:
                    key, value = header_str.split(':', 1)
                    current_endpoint['headers'][key.strip()] = value.strip()
                i += 2
            else:
                i += 1
        elif token in ['-d', '--data', '--data-raw', '--data-binary']:
            if i + 1 < len(tokens):
                body_str = tokens[i+1]
                try:
                    current_endpoint['body'] = json.loads(body_str)
                except:
                    current_endpoint['body'] = body_str
                if current_endpoint['method'] == 'GET':
                    current_endpoint['method'] = 'POST'
                i += 2
            else:
                i += 1
        elif not token.startswith('-') and not current_endpoint['url']:
            current_endpoint['url'] = token
            i += 1
        else:
            i += 1
            
    if current_endpoint:
        endpoints.append(current_endpoint)
        
    return endpoints