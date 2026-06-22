"""Quick smoke test for the direct search MCP."""
import asyncio
import json
from android_dri_mcp_server.search_tools import search_tsgs, search_icms, get_incident


async def main():
    print("=== TSG Search ===")
    result = await search_tsgs("broker silent token acquisition failure", max_results=3, index="msal-broker")
    parsed = json.loads(result)
    for r in parsed:
        score = r.get("score", 0)
        title = r.get("title", "")[:80]
        print(f"  [{score:.2f}] {title}")
    print(f"Total TSG results: {len(parsed)}\n")

    print("=== ICM Search ===")
    result = await search_icms("PRT acquisition failure in Authenticator", max_results=3, index="all")
    parsed = json.loads(result)
    for r in parsed:
        score = r.get("score", 0)
        title = r.get("title", "")[:80]
        ticket_id = r.get("ticket_id", "")
        print(f"  [{score:.2f}] {ticket_id} - {title}")
    print(f"Total ICM results: {len(parsed)}\n")

    # Use a ticket_id from the ICM search results for get_incident
    if parsed:
        tid = parsed[0].get("ticket_id", "")
        if tid:
            print(f"=== Get Incident {tid} ===")
            result = await get_incident(tid)
            incident = json.loads(result)
            print(f"  Title: {incident.get('title', '')[:80]}")
            print(f"  Team: {incident.get('owning_team', '')}")
            print(f"  Created: {incident.get('created', '')}")
            print(f"  Summary: {incident.get('summary', '')[:120]}...")
            print()

    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
