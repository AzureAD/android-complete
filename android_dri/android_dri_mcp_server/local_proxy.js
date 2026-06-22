// Local reverse proxy for MCP server
// Forwards requests from localhost to the hosted Container Apps MCP server.
// Works around VS Code Electron's fetch failing on the remote TLS connection.
const http = require("http");
const https = require("https");
const { URL } = require("url");

const REMOTE = "https://android-dri-mcp.proudbeach-7e7ce77d.eastus.azurecontainerapps.io";
const LOCAL_PORT = 3939;

// Keep a persistent HTTPS agent to reuse TLS sessions
const agent = new https.Agent({ keepAlive: true, maxSockets: 10 });

const server = http.createServer((req, res) => {
  const target = new URL(req.url, REMOTE);

  // Buffer the incoming request body first
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const body = Buffer.concat(chunks);

    const headers = { ...req.headers, host: target.hostname };
    delete headers["connection"];
    delete headers["transfer-encoding"];
    if (body.length > 0) {
      headers["content-length"] = body.length;
    }

    const options = {
      hostname: target.hostname,
      port: 443,
      path: target.pathname + target.search,
      method: req.method,
      headers,
      agent,
    };

    const proxy = https.request(options, (proxyRes) => {
      // For SSE responses, stream them through
      res.writeHead(proxyRes.statusCode, proxyRes.headers);
      proxyRes.pipe(res, { end: true });
    });

    proxy.on("error", (e) => {
      console.error("Proxy error:", e.message);
      if (!res.headersSent) {
        res.writeHead(502);
        res.end("Bad Gateway");
      }
    });

    if (body.length > 0) {
      proxy.end(body);
    } else {
      proxy.end();
    }
  });
});

server.listen(LOCAL_PORT, "127.0.0.1", () => {
  console.log(`MCP proxy listening on http://127.0.0.1:${LOCAL_PORT}`);
  console.log(`Forwarding to ${REMOTE}`);
});
