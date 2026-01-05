function generateK6Script(inputs) {
  const { api_collection, workload_scenario, sla, env } = inputs;

  let script = `import http from 'k6/http';\n`;
  script += `import { check, sleep } from 'k6';\n`;
  script += `import { Trend, Counter } from 'k6/metrics';\n\n`;

  // Options
  script += `export const options = {\n`;

  // Scenarios
  if (workload_scenario) {
    script += `  scenarios: {\n`;
    script += `    default: ${JSON.stringify(workload_scenario, null, 4)}\n`;
    script += `  },\n`;
  } else {
    script += `  // TODO: Workload scenario missing in input. Configure manually.\n`;
  }

  // Thresholds (SLA)
  if (sla) {
    script += `  thresholds: {\n`;
    if (sla.http_req_duration_p95_ms) {
      script += `    'http_req_duration': ['p(95)<${sla.http_req_duration_p95_ms}'],\n`;
    }
    if (sla.http_req_failed_rate_max !== undefined) {
      script += `    'http_req_failed': ['rate<${sla.http_req_failed_rate_max}'],\n`;
    }
    script += `  },\n`;
  }

  script += `};\n\n`;

  // Environment
  const baseUrl = env && env.base_url ? env.base_url : "http://localhost";

  script += `const BASE_URL = '${baseUrl}';\n\n`;

  script += `export default function() {\n`;

  if (api_collection && api_collection.endpoints) {
    api_collection.endpoints.forEach((ep) => {
      const method = ep.method.toLowerCase();
      const url = ep.url.startsWith("http") ? ep.url : `\${BASE_URL}${ep.url}`;
      const headers = ep.headers ? JSON.stringify(ep.headers) : "{}";
      const body = ep.body ? JSON.stringify(ep.body) : "null";

      script += `  {\n`;
      script += `    let params = { headers: ${headers} };\n`;
      script += `    let res = http.${method}(\`${url}\`, ${
        body !== "null" ? body : "null"
      }, params);\n`;
      script += `    check(res, { 'status is 2xx': (r) => r.status >= 200 && r.status < 300 });\n`;
      script += `    sleep(1);\n`;
      script += `  }\n`;
    });
  } else {
    script += `  // TODO: Logic for OpenAPI/Postman collection execution.\n`;
    script += `  // For full OpenAPI support, consider using k6-openapi-generator in the pipeline.\n`;
  }

  script += `}\n`;
  return script;
}

module.exports = { generateK6Script };
