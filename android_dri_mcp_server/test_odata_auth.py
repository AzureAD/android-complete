"""Quick diagnostic: test ICM OData cert-based auth."""
import os
import sys
import traceback

print(f"AZURE_CLIENT_ID = {os.environ.get('AZURE_CLIENT_ID', '<NOT SET>')}")
print(f"Python = {sys.version}")

try:
    from android_dri_mcp_server.icm_odata import icm_client

    print("Downloading certificate from Key Vault...")
    cert_path, key_path = icm_client._ensure_cert()
    print(f"Cert: {cert_path}  Key: {key_path}")

    print("Calling ICM OData /api/cert/incidents(21000000961520) ...")
    result = icm_client.get_incident(21000000961520)
    if result:
        print(f"SUCCESS: got incident title = {result.get('Title', result.get('title', 'N/A'))}")
        print(f"Keys: {list(result.keys())[:15]}")
    else:
        print("FAILED: get_incident returned None (check logs above)")
except Exception as e:
    print(f"FAILED: {e}")
    traceback.print_exc()
