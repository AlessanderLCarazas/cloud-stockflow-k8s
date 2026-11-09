import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
    stages: [
        { duration: '30s', target: 50 },
        { duration: '1m', target: 150 },
        { duration: '20s', target: 0 },
    ],
};
// Probando mi primer push desde la rama Princce
export default function () {
    let res = http.get('http://backend-service:3000/api/stress');
    check(res, { 'Servidor responde': (r) => r.status == 200 });
    sleep(0.1); 
}