import json
import re

def generate_k6_script(inputs):
    api_collection = inputs.get('api_collection')
    workload_scenario = inputs.get('workload_scenario')
    sla = inputs.get('sla')
    env = inputs.get('env')

    script = []
    script.append("import http from 'k6/http';")
    script.append("import { check, sleep, group } from 'k6';")
    script.append("import { Trend, Counter } from 'k6/metrics';")
    script.append("const errorRate = new Counter('errors');\n")

    script.append("export const options = {")
    
    if workload_scenario:
        script.append("  scenarios: {")
        script.append(f"    default: {json.dumps(workload_scenario, indent=4)}")
        script.append("  },")
    else:
        script.append("  // TODO: Workload scenario missing in input. Configure manually.")

    if sla:
        script.append("  thresholds: {")
        if sla.get('http_req_duration_p95_ms'):
            script.append(f"    'http_req_duration': ['p(95)<{sla['http_req_duration_p95_ms']}'],")
        if sla.get('http_req_failed_rate_max') is not None:
            script.append(f"    'http_req_failed': ['rate<{sla['http_req_failed_rate_max']}'],")
        script.append("  },")
    
    script.append("};\n")

    base_url = env.get('base_url', 'http://localhost') if env else 'http://localhost'
    script.append(f"const BASE_URL = '{base_url}';\n")

    def resolve_vars(target):
        if not env or not env.get('variables'):
            return target
        
        variables = env['variables']
        
        if isinstance(target, str):
            def replacer(match):
                key = match.group(1)
                return str(variables.get(key, match.group(0)))
            return re.sub(r'\{\{([^}]+)\}\}', replacer, target)
        
        if isinstance(target, list):
            return [resolve_vars(i) for i in target]
        
        if isinstance(target, dict):
            return {k: resolve_vars(v) for k, v in target.items()}
        
        return target

    script.append("export default function() {")

    if api_collection and api_collection.get('endpoints'):
        for ep in api_collection['endpoints']:
            method = ep['method'].lower()
            
            # Handle Postman variables in URL
            processed_url = re.sub(r'\{\{(?:base_?url|host|api_?url)\}\}', '${BASE_URL}', ep['url'], flags=re.IGNORECASE)
            
            processed_url = resolve_vars(processed_url)

            if processed_url.startswith('http') or processed_url.startswith('${BASE_URL}'):
                url = processed_url
            else:
                url = f"${{BASE_URL}}{processed_url}"
            
            resolved_headers = resolve_vars(ep.get('headers') or {})
            resolved_body = resolve_vars(ep.get('body'))
            
            headers_str = json.dumps(resolved_headers)
            body_str = json.dumps(resolved_body) if resolved_body else 'null'

            script.append(f"  group('{ep.get('name', ep['url'])}', function() {{")
            script.append(f"    let params = {{ headers: {headers_str}, tags: {{ name: '{ep.get('name', 'Unnamed Request')}' }} }};")
            script.append(f"    let res = http.{method}(`{url}`, {body_str}, params);")
            script.append("    const checkRes = check(res, { 'status is 2xx': (r) => r.status >= 200 && r.status < 300 });")
            script.append("    if (!checkRes) { errorRate.add(1); }")
            script.append("    sleep(1);")
            script.append("  });")
    else:
        script.append("  // TODO: Logic for OpenAPI/Postman collection execution.")
    
    script.append("}")
    
    return "\n".join(script)