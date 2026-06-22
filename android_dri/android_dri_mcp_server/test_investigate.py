"""Quick test for batch_search."""
import asyncio, time, json
from android_dri_mcp_server.search_tools import batch_search

searches = json.dumps([
    {"type": "icm", "query": "Authenticator not receiving code push notification not working", "index": "auth-app"},
    {"type": "icm", "query": "Authenticator MFA code not delivered mobile device", "index": "msal-broker"},
    {"type": "tsg", "query": "Authenticator push notification not received code not working", "index": "auth-app"},
    {"type": "tsg", "query": "MFA verification code not delivered Authenticator app troubleshooting", "index": "all"},
])

t0 = time.time()
r = asyncio.run(batch_search(searches))
elapsed = time.time() - t0
d = json.loads(r)
print(f"Time: {elapsed:.2f}s")
print(f"Payload size: {len(r)} bytes ({len(r)/1024:.1f} KB)")
for s in d.get("searches", []):
    print(f"  [{s['type']}] index={s['index']} query={s['query'][:50]}... => {len(s['results'])} results")
    if s['results']:
        print(f"    top score={s['results'][0].get('score', 0):.4f}  {str(s['results'][0].get('title', ''))[:60]}")
