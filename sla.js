function evaluateSLA(metrics, slaConfig) {
  const results = {
    pass: true,
    verdicts: {},
  };

  if (!slaConfig) return results;

  // http_req_duration p95
  if (slaConfig.http_req_duration_p95_ms) {
    const actual = metrics.metrics.http_req_duration.values["p(95)"];
    const passed = actual <= slaConfig.http_req_duration_p95_ms;
    results.verdicts.http_req_duration_p95 = {
      expected: slaConfig.http_req_duration_p95_ms,
      actual: actual,
      pass: passed,
    };
    if (!passed) results.pass = false;
  }

  // http_req_failed rate
  if (slaConfig.http_req_failed_rate_max !== undefined) {
    const actual = metrics.metrics.http_req_failed.values.rate;
    const passed = actual <= slaConfig.http_req_failed_rate_max;
    results.verdicts.http_req_failed_rate = {
      expected: slaConfig.http_req_failed_rate_max,
      actual: actual,
      pass: passed,
    };
    if (!passed) results.pass = false;
  }

  // Throughput (RPS) - Approximation
  // k6 JSON output usually has http_reqs.values.rate (reqs/s)

  return results;
}

module.exports = { evaluateSLA };
