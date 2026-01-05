def evaluate_sla(metrics, sla_config):
    results = {
        "pass": True,
        "verdicts": {}
    }

    if not sla_config:
        return results

    # http_req_duration p95
    if sla_config.get('http_req_duration_p95_ms'):
        # k6 summary export structure: metrics -> http_req_duration -> values -> p(95)
        try:
            actual = metrics['metrics']['http_req_duration']['values']['p(95)']
            passed = actual <= sla_config['http_req_duration_p95_ms']
            results['verdicts']['http_req_duration_p95'] = {
                "expected": sla_config['http_req_duration_p95_ms'],
                "actual": actual,
                "pass": passed
            }
            if not passed:
                results['pass'] = False
        except KeyError:
            pass # Metric might not exist if test failed early

    # http_req_failed rate
    if sla_config.get('http_req_failed_rate_max') is not None:
        try:
            actual = metrics['metrics']['http_req_failed']['values']['rate']
            passed = actual <= sla_config['http_req_failed_rate_max']
            results['verdicts']['http_req_failed_rate'] = {
                "expected": sla_config['http_req_failed_rate_max'],
                "actual": actual,
                "pass": passed
            }
            if not passed:
                results['pass'] = False
        except KeyError:
            pass

    return results