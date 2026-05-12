# Extends the MCP server image — same base, same S360 item.
# Only adds the 3 extra packages the indexer needs (kusto, blob, tiktoken).
FROM androiddrimcp.azurecr.io/android-dri-mcp:latest

# Install additional indexer-only dependencies
COPY requirements-extra.txt .
RUN pip install --no-cache-dir -r requirements-extra.txt

# Copy indexer source alongside the MCP server source
COPY . ./android_dri_indexer/

# Override the MCP server entrypoint
ENTRYPOINT ["python3", "-m", "android_dri_indexer"]
CMD ["--config", "android_dri_indexer/configs/config_android.json", "--icm", "--skip-index-setup"]
