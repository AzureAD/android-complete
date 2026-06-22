FROM mcr.microsoft.com/azurelinux/base/python:3.12

# Patch ALL OS-level packages to latest available versions.
# This eliminates the need to chase individual CVEs (libarchive, nghttp2, etc.)
# every time a new scan fires. The layer cache will rebuild when the base image
# or Dockerfile changes.
RUN tdnf update -y && tdnf clean all

WORKDIR /app

# Install dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy package source (Dockerfile lives at the package root)
COPY . ./android_dri_mcp_server/

ENV PYTHONPATH=/app
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080

EXPOSE 8080

CMD ["python3", "-m", "android_dri_mcp_server"]
