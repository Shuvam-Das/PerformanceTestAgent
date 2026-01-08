import json
import re

def generate_k6_script(inputs):
    api_collection = inputs.get('api_collection')
    workload_scenario = inputs.get('workload_scenario')
    sla = inputs.get('sla')
    env = inputs.get('env')
    test_data = inputs.get('test_data')

    script = []
    script.append("import http from 'k6/http';")
    script.append("import { check, sleep, group } from 'k6';")
    script.append("import { Trend, Counter } from 'k6/metrics';")
    
    if test_data:
        script.append("import { SharedArray } from 'k6/data';")
        script.append("import papaparse from 'https://jslib.k6.io/papaparse/5.1.1/index.js';")

    script.append("const errorRate = new Counter('errors');\n")
    script.append("// Custom Trend metrics for endpoints")
    script.append("const responseTime = new Trend('http_req_duration_custom');\n")
    script.append("const checkFailureRate = new Rate('check_failure_rate');\n")

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

    # Data Loading
    if test_data:
        csv_filename = test_data.get('file', 'data.csv')
        script.append(f"const csvData = new SharedArray('test_data', function() {{")
        script.append(f"  return papaparse.parse(open('./{csv_filename}'), {{ header: true }}).data;")
        script.append("});\n")

    def resolve_vars(target):
        # If no env vars and no test data, return as is
        if (not env or not env.get('variables')) and not test_data:
            return target
        
        variables = env.get('variables', {}) if env else {}
        
        if isinstance(target, str):
            def replacer(match):
                key = match.group(1)
                # Priority 1: Env variables (Static)
                if key in variables:
                    return str(variables[key])
                # Priority 2: CSV Data (Dynamic)
                if test_data:
                    return f"${{datum['{key}']}}"
                # Priority 3: Correlated Variables (Runtime)
                return f"${{vars['{key}'] || '{match.group(0)}'}}"
                return match.group(0)
            return re.sub(r'\{\{([^}]+)\}\}', replacer, target)
        
        if isinstance(target, list):
            return [resolve_vars(i) for i in target]
        
        if isinstance(target, dict):
            return {k: resolve_vars(v) for k, v in target.items()}
        
        return target

    script.append("export default function() {")
    script.append("  // Runtime variables for correlation")
    script.append("  let vars = {};")
    
    if test_data:
        # Distribute data across VUs and Iterations
        script.append("  // Select data row based on VU and Iteration for unique usage")
        script.append("  const datum = csvData[(__VU - 1 + __ITER) % csvData.length];")

    if api_collection and api_collection.get('endpoints'):
        for ep in api_collection['endpoints']:
            # GraphQL Support
            if ep.get('graphql'):
                method = 'POST'
                gql = ep['graphql']
                # Construct body structure for GraphQL
                ep['body'] = {
                    'query': gql.get('query', ''),
                    'variables': gql.get('variables', {})
                }
                if not ep.get('headers'):
                    ep['headers'] = {}
                ep['headers']['Content-Type'] = 'application/json'
            else:
                method = ep['method'].upper()
            
            # Handle Postman variables in URL
            processed_url = re.sub(r'\{\{(?:base_?url|host|api_?url)\}\}', '${BASE_URL}', ep['url'], flags=re.IGNORECASE)
            
            processed_url = resolve_vars(processed_url)

            # Construct URL string
            if processed_url.startswith('http') or processed_url.startswith('${BASE_URL}'):
                url = processed_url
            elif processed_url.startswith('`') and processed_url.endswith('`'):
                 # Already a template string from resolve_vars
                 url = f"${{BASE_URL}}{processed_url[1:-1]}"
            else:
                url = f"${{BASE_URL}}{processed_url}"
            
            resolved_headers = resolve_vars(ep.get('headers') or {})
            resolved_body = resolve_vars(ep.get('body'))
            
            headers_str = json.dumps(resolved_headers)
            # If body contains dynamic variables (template string syntax), we need to remove quotes around it in the generated JS
            body_str = json.dumps(resolved_body) if resolved_body else 'null'
            
            # Fix for dynamic body: if body string has ${datum...}, json.dumps escapes it. 
            # We need to unescape the template literal syntax for JS execution.
            if test_data and '${datum' in body_str:
                body_str = body_str.replace('"${', '`${').replace('}"', '}`').replace('\\"', '"')

            script.append(f"  group('{ep.get('name', ep['url'])}', function() {{")
            script.append(f"    let params = {{ headers: {headers_str}, tags: {{ name: '{ep.get('name', 'Unnamed Request')}' }} }};")
            script.append(f"    let res = http.request('{method}', `{url}`, {body_str}, params);")
            
            # Industry standard checks
            script.append("    const checkRes = check(res, {")
            script.append("      'status is 2xx': (r) => r.status >= 200 && r.status < 300,")
            
            # Custom assertions from input
            if ep.get('assertions'):
                for assertion in ep['assertions']:
                    # Example: "status == 201", "time < 500"
                    if '==' in assertion:
                        parts = assertion.split('==')
                        key = parts[0].strip()
                        val = parts[1].strip()
                        if key == 'status':
                            script.append(f"      'status is {val}': (r) => r.status == {val},")
                    elif '<' in assertion:
                        parts = assertion.split('<')
                        key = parts[0].strip()
                        val = parts[1].strip()
                        if key == 'time':
                            script.append(f"      'time < {val}ms': (r) => r.timings.duration < {val},")
            
            script.append("    });")
            
            script.append("    checkFailureRate.add(!checkRes);")
            script.append("    responseTime.add(res.timings.duration);")
            script.append("    if (!checkRes) { errorRate.add(1); }")
            
            # Correlation / Extraction
            if ep.get('extract'):
                for var_name, source in ep['extract'].items():
                    # source format: "json:path.to.key" or "header:Header-Name" or "regex:pattern"
                    if source.startswith('json:'):
                        path = source.split(':', 1)[1]
                        script.append(f"    try {{ vars['{var_name}'] = res.json('{path}'); }} catch(e) {{}}")
                    elif source.startswith('header:'):
                        header = source.split(':', 1)[1]
                        script.append(f"    vars['{var_name}'] = res.headers['{header}'];")
                    elif source.startswith('regex:'):
                        pattern = source.split(':', 1)[1]
                        script.append(f"    let match_{var_name} = res.body.match(/{pattern}/);")
                        script.append(f"    if (match_{var_name}) vars['{var_name}'] = match_{var_name}[1];")

            # Think Time / Pacing (Randomized 1-3s)
            script.append("    sleep(Math.random() * 2 + 1);")
            script.append("  });")
    else:
        script.append("  // TODO: Logic for OpenAPI/Postman collection execution.")
    
    script.append("}")
    
    return "\n".join(script)