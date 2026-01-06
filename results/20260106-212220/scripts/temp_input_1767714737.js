import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Trend, Counter } from 'k6/metrics';
const errorRate = new Counter('errors');

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
  group('https://httpbin.org/get', function() {
    let params = { headers: {}, tags: { name: 'Unnamed Request' } };
    let res = http.get(`https://httpbin.org/get`, null, params);
    const checkRes = check(res, { 'status is 2xx': (r) => r.status >= 200 && r.status < 300 });
    if (!checkRes) { errorRate.add(1); }
    sleep(1);
  });
}