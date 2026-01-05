const fs = require("fs");
const yaml = require("js-yaml");
const axios = require("axios");

async function parseInput(config) {
  let content = "";
  const diagnostics = [];

  // 1. Input Ingestion
  if (config.jira) {
    try {
      const auth = config.jira.auth; // Assuming basic auth or token
      const response = await axios.get(
        `${config.jira.base_url}/rest/api/2/issue/${config.jira.issue_key}`,
        {
          headers: { Authorization: auth },
        }
      );
      content = response.data.fields.description || "";
      // Check attachments could go here
    } catch (e) {
      diagnostics.push({
        path: "$.jira",
        reason: `Failed to fetch Jira issue: ${e.message}`,
      });
    }
  } else if (config.file) {
    try {
      content = fs.readFileSync(config.file, "utf8");
    } catch (e) {
      diagnostics.push({
        path: "$.file",
        reason: `Failed to read local file: ${e.message}`,
      });
    }
  } else {
    diagnostics.push({
      path: "$",
      reason: "No input source provided (Jira or File)",
    });
  }

  if (diagnostics.length > 0) return { result: null, diagnostics };

  const result = {
    api_collection: null,
    workload_scenario: null,
    sla: null,
    env: null,
  };

  // 2. Parsing Logic
  // Detect fenced code blocks
  const jsonBlocks = content.match(/```json([\s\S]*?)```/g) || [];
  const yamlBlocks = content.match(/```yaml([\s\S]*?)```/g) || [];
  const plainBlocks = content.match(/```([\s\S]*?)```/g) || []; // Fallback

  const tryParse = (str, fmt) => {
    try {
      return fmt === "yaml" ? yaml.load(str) : JSON.parse(str);
    } catch (e) {
      return null;
    }
  };

  const allBlocks = [...jsonBlocks, ...yamlBlocks];
  // If no explicit json/yaml blocks, try generic blocks
  if (allBlocks.length === 0) allBlocks.push(...plainBlocks);

  // Fallback: If no blocks found, try parsing the entire content
  if (allBlocks.length === 0 && content.trim()) {
    allBlocks.push(content);
  }

  allBlocks.forEach((block, index) => {
    const isYaml = block.includes("yaml");
    const clean = block.replace(/```(json|yaml)?/g, "").trim();
    const obj = tryParse(clean, isYaml ? "yaml" : "json");

    if (obj) {
      // Heuristics to identify object type
      if (obj.openapi || obj.swagger) {
        result.api_collection = { openapi: obj };
      } else if (obj.info && obj.item) {
        result.api_collection = { postman: obj };
      } else if (obj.api_collection && obj.api_collection.endpoints) {
        result.api_collection = { endpoints: obj.api_collection.endpoints };
      } else if (Array.isArray(obj) && obj[0] && obj[0].method && obj[0].url) {
        result.api_collection = { endpoints: obj };
      }

      if (obj.workload_scenario)
        result.workload_scenario = obj.workload_scenario;
      else if (
        obj.type &&
        (obj.type === "constant-vus" || obj.type === "ramping-vus")
      )
        result.workload_scenario = obj;

      if (obj.sla) result.sla = obj.sla;
      else if (obj.http_req_duration_p95_ms || obj.throughput_rps_min)
        result.sla = obj;

      if (obj.env) result.env = obj.env;
    }
  });

  // 3. Endpoint Normalization / Dedup (if endpoints exist)
  if (result.api_collection && result.api_collection.endpoints) {
    const unique = new Map();
    result.api_collection.endpoints.forEach((ep, idx) => {
      if (!ep.method || !ep.url) {
        diagnostics.push({
          path: `$.api_collection.endpoints[${idx}]`,
          reason: "Missing method or url",
        });
        return;
      }
      // Normalize path params (simple regex for /123 -> /{id})
      const normalizedUrl = ep.url.replace(/\/\d+/g, "/{id}");
      const key = `${ep.method.toUpperCase()}:${normalizedUrl}`;
      if (!unique.has(key)) {
        unique.set(key, ep);
      }
    });
    result.api_collection.endpoints = Array.from(unique.values());
  }

  return { result, diagnostics };
}

module.exports = { parseInput };
