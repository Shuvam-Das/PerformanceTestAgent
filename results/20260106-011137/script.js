import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter } from 'k6/metrics';

export const options = {
  scenarios: {
    default: {
    "executor": "constant-vus",
    "vus": 1,
    "duration": "1s"
}
  },
  thresholds: {
    'http_req_duration': ['p(95)<500'],
  },
};

const BASE_URL = 'http://localhost';

export default function() {
  {
    let params = { headers: {} };
    let res = http.get(`https://httpbin.org/get`, null, params);
    check(res, { 'status is 2xx': (r) => r.status >= 200 && r.status < 300 });
    sleep(1);
  }
}