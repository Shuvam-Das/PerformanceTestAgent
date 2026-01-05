const fs = require("fs");
const path = require("path");
const shell = require("shelljs");
const yargs = require("yargs/yargs");
const { hideBin } = require("yargs/helpers");

const { parseInput } = require("./parser");
const { generateK6Script } = require("./generator");
const { evaluateSLA } = require("./sla");

const argv = yargs(hideBin(process.argv))
  .option("jira_key", { type: "string" })
  .option("jira_url", { type: "string" })
  .option("jira_auth", { type: "string" })
  .option("file", { type: "string" })
  .option("output_dir", { type: "string", default: "./results" }).argv;

async function run() {
  // 0. Setup
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const resultDir = path.join(argv.output_dir, timestamp);
  shell.mkdir("-p", resultDir);

  console.log(`[STATUS] Starting Agent. Results: ${resultDir}`);

  // 1. Input Ingestion & Parsing
  console.log(`[STATUS] Stage: Input Ingestion`);
  const config = {
    jira: argv.jira_key
      ? {
          base_url: argv.jira_url,
          issue_key: argv.jira_key,
          auth: argv.jira_auth,
        }
      : null,
    file: argv.file,
  };

  const { result: inputs, diagnostics } = await parseInput(config);

  // 8. Understanding Diagnostics (Fail if parsing failed critically)
  if (!inputs && diagnostics.length > 0) {
    console.log("Unable to understand requirements");
    console.log(JSON.stringify(diagnostics, null, 2));
    process.exit(1);
  }

  // Save snapshot
  fs.writeFileSync(
    path.join(resultDir, "input_snapshot.json"),
    JSON.stringify(inputs, null, 2)
  );

  // 2. API Collection Validation
  console.log(`[STATUS] Stage: API Collection Validation`);
  if (!inputs.api_collection) {
    console.log("Human Intervention Required: API collection missing");
    process.exit(1);
  }

  // Linting (Spectral/Ajv) - Simplified for this agent
  // If OpenAPI, we would run: shell.exec('spectral lint ...')
  // Here we just log success if parsed.
  console.log(
    `[STATUS] API Collection detected: ${Object.keys(inputs.api_collection)[0]}`
  );

  // 3. k6 Script Generation
  console.log(`[STATUS] Stage: k6 Script Generation`);
  const scriptContent = generateK6Script(inputs);
  const scriptPath = path.join(resultDir, "script.js");
  fs.writeFileSync(scriptPath, scriptContent);

  // 4. Script Validation
  console.log(`[STATUS] Stage: Script Validation`);
  // ESLint
  const lintRes = shell.exec(`npx eslint "${scriptPath}"`, { silent: true });
  if (lintRes.code !== 0) {
    console.log("Unable to understand requirements");
    console.log("Diagnostics: Generated script failed linting.");
    console.log(lintRes.stdout);
    fs.writeFileSync(path.join(resultDir, "lint_report.txt"), lintRes.stdout);
    process.exit(1);
  }

  // Smoke Run
  console.log(`[STATUS] Stage: Smoke Run`);
  const smokeRes = shell.exec(`k6 run --vus 1 --duration 1s "${scriptPath}"`, {
    silent: true,
  });
  fs.writeFileSync(
    path.join(resultDir, "smoke_run_output.txt"),
    smokeRes.stdout + smokeRes.stderr
  );

  if (smokeRes.code !== 0) {
    console.log("Unable to understand requirements");
    console.log("Diagnostics: Script smoke run failed.");
    process.exit(1);
  }

  // 5. Workload Scenario (Handled in generation, just logging here)
  if (!inputs.workload_scenario) {
    console.log("[INFO] No workload scenario provided. Using defaults/TODOs.");
  }

  // 6. SLA Check
  console.log(`[STATUS] Stage: SLA Check`);
  if (!inputs.sla) {
    console.log("Human Intervention Required: SLA missing");
    process.exit(1);
  }

  // 7. SLA Actions (Run Test)
  console.log(`[STATUS] Stage: Full Test Execution`);
  const summaryPath = path.join(resultDir, "test_results.json");
  const summaryExportPath = path.join(resultDir, "summary_export.json");
  // Run k6 and output JSON
  const testRes = shell.exec(
    `k6 run --out json="${summaryPath}" --summary-export="${summaryExportPath}" "${scriptPath}"`
  );

  if (testRes.code !== 0) {
    console.log("[ERROR] Test execution failed.");
    process.exit(1);
  }

  // 12. SLA Evaluation
  console.log(`[STATUS] Stage: SLA Evaluation`);

  let metrics = {};
  try {
    metrics = JSON.parse(fs.readFileSync(summaryExportPath, "utf8"));
  } catch (e) {
    console.log("[ERROR] Failed to read test summary.");
  }

  const slaResults = evaluateSLA(metrics, inputs.sla);
  fs.writeFileSync(
    path.join(resultDir, "sla_validation.json"),
    JSON.stringify(slaResults, null, 2)
  );

  // Final Summary
  const summaryMd = `
# Test Summary

**Date**: ${timestamp}
**Input**: ${argv.file || argv.jira_key}

## Validation
- Script Lint: PASS
- Smoke Run: PASS

## SLA Verdict
**Overall Pass**: ${slaResults.pass}

### Metrics
\`\`\`json
${JSON.stringify(slaResults.verdicts, null, 2)}
\`\`\`

## Artifacts
- Script
- Results
    `;
  fs.writeFileSync(path.join(resultDir, "summary.md"), summaryMd);

  console.log(`[STATUS] Pipeline Complete.`);
  console.log(`Artifacts saved in: ${resultDir}`);
  if (!slaResults.pass) {
    console.log("[WARN] SLA Failed.");
  } else {
    console.log("[SUCCESS] SLA Passed.");
  }
}

run();
