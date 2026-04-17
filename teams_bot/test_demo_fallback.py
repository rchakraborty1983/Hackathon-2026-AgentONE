"""Quick test for demo fallback features."""
import requests, time, os

KEY = os.environ.get("AGENTONE_API_KEY", "")
BASE = "http://localhost:9100/chat"
HEADERS = {"x-api-key": KEY}

def chat(msg):
    r = requests.post(BASE, json={"message": msg}, headers=HEADERS, timeout=60)
    return r.status_code, r.json().get("response", "")

print("=" * 60)
print("TEST 1: Build failure 812833 demo fallback")
print("=" * 60)
t0 = time.time()
code, resp = chat("analyze build failure 812833")
print(f"START: {code} ({time.time()-t0:.1f}s)")
print(resp[:300])
print()

time.sleep(5)

t1 = time.time()
code2, resp2 = chat("build failure status 812833")
print(f"STATUS: {code2} ({time.time()-t1:.1f}s)")
print(resp2[:500])
print()

print("=" * 60)
print("TEST 2: GHAS fix PR for alert #747")
print("=" * 60)
t2 = time.time()
code3, resp3 = chat("fix alert #747 in GHAS-POC-AngularClient and create a PR")
print(f"START: {code3} ({time.time()-t2:.1f}s)")
print(resp3[:500])
print()

# Wait and check status
time.sleep(30)
t3 = time.time()
code4, resp4 = chat("PR status for alert #747 in GHAS-POC-AngularClient")
print(f"STATUS: {code4} ({time.time()-t3:.1f}s)")
print(resp4[:500])
print()

print("=" * 60)
print("ALL TESTS COMPLETE")
print("=" * 60)
