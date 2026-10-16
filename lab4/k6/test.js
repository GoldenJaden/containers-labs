import http from 'k6/http';
import { check } from 'k6';

export const options = {
  insecureSkipTLSVerify: true,
  scenarios: {
    orders: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.ORDER_RATE || 5),
      timeUnit: '1s',
      duration: __ENV.TEST_DURATION || '10m',
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

export default function () {
  const response = http.post(
    'https://lab-app.homelab.internal/order',
    JSON.stringify({
      item: 'coffee',
      quantity: 1,
    }),
    {
      headers: {
        'Content-Type': 'application/json',
      },
      tags: {
        name: 'POST /order',
      },
    },
  );

  check(response, {
    'order created': (response) => response.status === 201,
  });
}
