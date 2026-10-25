import requests, json
r = requests.get('http://127.0.0.1:8000/api/tasks/60e139b7-8b57-4926-84b4-2bd2a686b38d')
d = r.json()
print('status:', d['status'])
print('step_count:', d.get('step_count'))
print('tier:', d.get('current_tier'))
print('final_result:', json.dumps(d.get('final_result'), indent=2))
print()
for run in d.get('runs', []):
    print(f'Run {run["id"][:8]} | tier={run["tier"]} | status={run["status"]} | steps={run["step_count"]}')
    for s in run.get('steps', []):
        print(f'  step {s["step_number"]}: verify={s["verification_status"]} | fail={s.get("failure_type")} | thought={str(s.get("thought",""))[:80]}')
