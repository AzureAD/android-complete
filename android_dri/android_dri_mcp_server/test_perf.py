"""Quick perf test for parallel OData calls."""
import time
from android_dri_mcp_server.icm_odata import icm_client

# Warm up (cert already cached by server startup, but this test creates a new instance)
icm_client._get_session()

# Time get_full_incident (3 parallel OData calls)
start = time.time()
result = icm_client.get_full_incident("21000000961520")
elapsed = time.time() - start

if result:
    print(f"get_full_incident: {elapsed:.2f}s")
    print(f"  incident: {'yes' if result.get('incident') else 'no'}")
    print(f"  discussion entries: {len(result.get('discussion', []))}")
    print(f"  rca: {'yes' if result.get('rca') else 'no'}")
else:
    print(f"get_full_incident FAILED in {elapsed:.2f}s")

# Time single get_incident (for comparison)
start2 = time.time()
single = icm_client.get_incident("21000000961520")
elapsed2 = time.time() - start2
print(f"get_incident (single): {elapsed2:.2f}s")
print(f"  parallel overhead: {max(0, elapsed - elapsed2):.2f}s")
