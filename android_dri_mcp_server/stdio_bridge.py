"""Stdio-to-HTTP MCP bridge.

VS Code launches this as a stdio MCP server. It reads JSON-RPC messages
from stdin, forwards them to the remote streamable-HTTP MCP endpoint,
and writes responses back to stdout.

This works around VS Code windows that cannot connect to remote HTTP
MCP servers due to Electron TLS/networking issues.
"""

import sys
import json
import certifi
import httpx

REMOTE_URL = "https://android-dri-mcp.proudbeach-7e7ce77d.eastus.azurecontainerapps.io/mcp"
session_id = None
client = httpx.Client(timeout=120, http2=False, verify=certifi.where())


def send_http(payload: dict) -> dict | None:
    global session_id
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    try:
        resp = client.post(REMOTE_URL, json=payload, headers=headers)
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            session_id = sid

        content_type = resp.headers.get("Content-Type", "")

        if "text/event-stream" in content_type:
            results = []
            for line in resp.text.split("\n"):
                if line.startswith("data: "):
                    try:
                        results.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
            return results[0] if results else None
        else:
            return resp.json() if resp.text.strip() else None
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.stderr.flush()
        return {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32000, "message": str(e)}}


def write_message(msg: dict):
    data = json.dumps(msg)
    sys.stdout.write(data + "\n")
    sys.stdout.flush()


def main():
    sys.stderr.write("MCP stdio-to-HTTP bridge starting\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"Invalid JSON: {line[:100]}\n")
            sys.stderr.flush()
            continue

        response = send_http(request)
        if response and "id" in request:
            # Ensure the response ID matches
            response["id"] = request["id"]
            write_message(response)
        elif "id" not in request:
            # Notification — send but don't expect response
            send_http(request)


if __name__ == "__main__":
    main()
