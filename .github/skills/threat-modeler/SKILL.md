---
name: threat-modeler
description: "Create threat model diagrams for Android Auth features. Supports three output modes: (A) new .tm7 file for Microsoft Threat Modeling Tool, (B) add diagram to existing .tm7, (C) Markdown export with Mermaid diagram for users without TMT. Optional STRIDE threat analysis. Triggers: 'create a threat model', 'threat model for', 'add threat diagram', 'threat model diagram', 'export threat model', 'STRIDE analysis for', 'security diagram for', or any request to create or update a threat model diagram."
---

# Threat Modeler

Create threat model diagrams from feature descriptions. The AI researches the codebase to identify components, trust boundaries, and data flows, then produces a JSON spec that scripts convert to the desired output format.

## Workflow

### Step 1: Understand the Feature

Research the feature using the codebase-researcher skill or subagent. Identify:
- **Processes**: Components that process data (Broker, MSAL, Chrome, eSTS)
- **Data stores**: Where tokens/keys are persisted
- **External interactors**: Systems outside our control
- **Trust boundaries**: App sandboxes, device boundary, network boundary
- **Data flows**: IPC calls, HTTPS requests, token exchanges

**IMPORTANT — Abstraction Level**: Model at trust-boundary and IPC-channel level, NOT at individual Activity/class level. A threat model is about security boundaries and data flows between them, not software architecture.

Bad (too granular):
- SwitchBrowserActivity, BrokerBrowserRedirectActivity, AuthStrategy as separate nodes

Good (security-relevant):
- "Broker" as one process, "System Browser" as one external interactor, with data flows showing the IPC/intent/HTTPS channels between them

**Rules of thumb:**
- If two components run in the same app sandbox, they're ONE process node
- Individual Activities, Fragments, or classes within the same process should NOT be separate nodes
- Focus on: what crosses a trust boundary? what crosses a network boundary? what touches a data store?
- Keep to 3-7 nodes for typical features. More complex features may need up to 10.

### Step 2: Determine Output Mode

Ask the user which mode they want (use askQuestion tool):

> How would you like the threat model delivered?
> - **A) New .tm7 file** — Creates a standalone file for Microsoft Threat Modeling Tool
> - **B) Add to existing .tm7** — Appends a new diagram tab to your existing threat model file (provide the file path)
> - **C) Markdown export** — Creates a threat-model.md with Mermaid diagram (no TMT needed). Also saves a spec.json for later .tm7 conversion

### Step 3: Build the JSON Spec

Produce a JSON spec with **explicit 2D positions** for nodes and **hand-placed handles** for edges. This produces clean, professional diagrams that match hand-built TMT quality.

```json
{
    "title": "Feature Name Flow",
    "nodes": [
        {"id": "broker", "name": "Broker", "type": "process", "x": 380, "y": 310},
        {"id": "ests", "name": "eSTS", "type": "process", "outOfScope": true, "x": 1100, "y": 200},
        {"id": "browser", "name": "System Browser", "type": "external", "x": 460, "y": 650}
    ],
    "boundaries": [
        {"id": "broker_sbx", "name": "Broker Sandbox", "type": "border", "contains": ["broker"]},
        {"id": "device", "name": "Android Device", "type": "border", "contains": ["broker_sbx", "browser_sbx"]},
        {"id": "internet", "name": "Internet Boundary", "type": "line", "line": {"x1": 920, "y1": 50, "x2": 880, "y2": 870, "hx": 870, "hy": 430}}
    ],
    "edges": [
        {"from": "broker", "to": "ests", "name": "1. /authorize",
         "hx": 780, "hy": 85, "portSource": "North", "portTarget": "NorthWest"},
        {"from": "ests", "to": "broker", "name": "2. Return token",
         "hx": 720, "hy": 210, "portSource": "West", "portTarget": "East"}
    ]
}
```

#### Node Properties
- **`type`**: `process` (circle), `datastore` (parallel lines), `external` (rectangle)
- **`outOfScope`**: Set `true` for components we don't own — TMT skips STRIDE threat generation
- **`x`, `y`**: Explicit position (top-left corner). ALWAYS provide these for 2D layout

#### Edge Properties
- **`hx`, `hy`**: Handle position (controls curve midpoint and label placement). ALWAYS provide these
- **`portSource`, `portTarget`**: Compass direction where arrow attaches to node (North, South, East, West, NorthEast, NorthWest, SouthEast, SouthWest). ALWAYS provide these
- **Naming**: Number steps sequentially (e.g., "1. Request", "2. Response")

#### Boundary Types
- **`"type": "border"`** — Dashed rectangle around contained nodes (app sandboxes, device boundary)
- **`"type": "line"`** — Dashed diagonal line separating zones (internet/network boundary). Use this for device↔cloud separation instead of a box. Requires `"line": {"x1", "y1", "x2", "y2", "hx", "hy"}`
- **Nesting**: Outer boundaries list inner boundaries in their `contains` array

Save the spec to a temporary JSON file.

#### Layout Design Principles

Follow these principles to produce clean, readable diagrams:

1. **Hub-spoke layout**: Place the hub node (the component with most edges) near the center. Radiate spokes outward to other nodes. Do NOT place all nodes in a single row.

2. **Distribute edges around hub nodes**: For any node with 4+ edges, **use all available compass directions** for handles and ports. Each edge connected to a hub node should approach from a unique 45° sector. Plan the port allocation before writing the spec:
   - List all edges touching the hub node
   - Assign each edge a unique compass port on that node (N, NE, E, SE, S, SW, W, NW = 8 slots)
   - Place each handle in the quadrant corresponding to its assigned port
   - Example for eSTS with 8 edges: edge 2→N, edge 3→NW, edge 5→S, edge 6→E, edge 7→SE, edge 8→SW, edge 10→NE, edge 11→W
   - The script detects port duplication and handle clustering, logging warnings if multiple edges share the same port or cluster within a 90° sector

3. **Line boundary for internet**: Use `"type": "line"` (not a box) to separate on-device nodes from cloud/server nodes. Place the line roughly vertically at x≈900. Device nodes go left, cloud nodes go right.

4. **100×100 node size**: The script defaults to 100px nodes (matching hand-built TMT diagrams). Don't override unless needed.

5. **Hand-place every edge**: Always provide `hx`, `hy`, `portSource`, `portTarget` for each edge. Algorithmic routing produces overlapping labels. Spread parallel edges by ~80px.

6. **Port direction rule**: Each port MUST face toward the handle position. The Bézier curve exits/enters the node from the port direction, curves through the handle, and reaches the other node. If a port faces away from the handle (>90° off), the curve will loop over the node creating a crossing. The script auto-corrects bad ports, but getting them right in the spec avoids surprises.
   - `portSource` = compass direction from **source center** toward the handle
   - `portTarget` = compass direction from **target center** toward the handle
   - Example: if handle is at (700, 460) and target center is at (1100, 230), the handle is to the SouthWest → use `portTarget: "SouthWest"`, NOT `"North"` or `"East"`

7. **Canvas limits**: Keep all elements within ~1200×900px. TMT's visible grid area is finite (~1800×1200px) and elements beyond it are not rendered.

8. **Boundary padding**: Leave ≥60px between a node and its inner sandbox boundary. Leave ≥80px between inner and outer boundaries so their labels don't overlap. The script auto-enforces a 30px minimum gap between nested boundary edges, but placing nodes ≥80px from the canvas origin avoids the script having to push outer boundaries into negative coordinates.

9. **Edge handle placement for parallel edges**: When multiple edges connect the same pair of nodes, offset their handles by ~80-100px along the axis between the nodes. Use different compass ports (e.g., North/NorthWest for one, East/West for another) to create visually distinct curves.

10. **Edge labels clear of boundaries**: Ensure no edge handle (hx, hy) falls near a boundary's top-left corner where the boundary name label appears (~20px below the boundary's Top coordinate).

11. **Short node names**: Use concise names ("Broker", "eSTS", "System Browser") — long names clutter the diagram.

### Step 4: Ask About STRIDE Analysis

Use askQuestion tool:

> Would you like me to generate a STRIDE threat analysis for this diagram?
> This identifies Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, and Elevation of Privilege threats for each data flow crossing a trust boundary.

If yes, generate STRIDE data. See [references/stride-guide.md](references/stride-guide.md) for patterns and common Android Auth threats. Save as a separate JSON:

```json
{
    "spoofing": [{"threat": "...", "dataflow": "...", "mitigation": "...", "status": "Mitigated"}],
    "tampering": [...],
    "repudiation": [...],
    "information_disclosure": [...],
    "denial_of_service": [...],
    "elevation_of_privilege": [...]
}
```

### Step 5: Generate Output

All output files go to `~/threat-models/` (create the directory if it doesn't exist). Use a descriptive filename based on the feature name (e.g., `browser-sso-threat-model.tm7`).

Run the appropriate script:

**Mode A** — New .tm7:
```bash
node scripts/create_tm7.js --spec /tmp/spec.json --output ~/threat-models/FeatureName-ThreatModel.tm7 --template assets/template.tm7
```

**Mode B** — Add to existing .tm7:
```bash
node scripts/add_diagram.js --file /path/to/existing.tm7 --spec /tmp/spec.json
```

**Mode C** — Markdown export:
```bash
node scripts/export_markdown.js --spec /tmp/spec.json --output ~/threat-models/FeatureName-threat-model.md [--stride /tmp/stride.json]
```

For Mode C, also inform the user they can convert to .tm7 later using the saved spec.json.

**All modes — always generate an MD report alongside:**
Regardless of the chosen mode, always also generate a Markdown report so users have a readable document with the diagram, component table, data flow table, and STRIDE analysis (if generated). For Modes A and B, run the markdown export as an additional step:
```bash
node scripts/export_markdown.js --spec /tmp/spec.json --output ~/threat-models/FeatureName-threat-model.md [--stride /tmp/stride.json]
```

### Step 6: Converting Mode C to .tm7 (on demand)

If a user later asks to convert from markdown, find the `threat-model-spec.json` saved alongside and run:
```bash
node scripts/create_tm7.js --spec threat-model-spec.json --output output.tm7 --template assets/template.tm7
```

## Script Paths

All script paths are relative to this skill's directory:
- `scripts/create_tm7.js` — Mode A: JSON spec → new .tm7
- `scripts/add_diagram.js` — Mode B: JSON spec → append to existing .tm7
- `scripts/export_markdown.js` — Mode C: JSON spec → Markdown + saved spec

Template asset: `assets/template.tm7` (empty .tm7 with KnowledgeBase, no diagrams)

## Key Rules

- **Canvas size**: Keep all elements within ~1200×900px usable area (TMT grid is ~1800×1200 but leave margin)
- **Node size**: 100×100px (default). Matches hand-built TMT diagrams
- **Coordinates**: All Left/Top/HandleX/HandleY must be >= 50
- **Boundary padding**: >= 60px around contained elements; >= 80px between nested boundaries. The script auto-enforces a 30px minimum gap between nested boundary edges
- **Node placement**: Place innermost nodes ≥80px from canvas origin to give outer boundaries room to wrap without hitting coordinates ≤0
- **Inner boundaries** must appear before outer boundaries in XML
- **Line boundaries** (internet) go in `<Lines>` section, not `<Borders>`
- **Self-loop edges** need different source/target ports
- **Always hand-place edges** with explicit `hx`, `hy`, `portSource`, `portTarget` — algorithmic edge routing produces overlapping labels
- **Port direction**: Ports must face the handle. The script auto-corrects ports >90° off from the handle direction to prevent curves crossing over nodes
- **Edge distribution on hub nodes**: For nodes with 4+ edges, assign each edge a unique compass port — the script warns on port duplication and handle clustering (3+ within 90°)
- **Hub-spoke layout**: Place the most-connected node at center; never use a 1D row of nodes
- **Always generate MD report**: Regardless of output mode (A/B/C), always produce a Markdown report alongside for readability
- For detailed TMT XML rules, see [references/tmt-xml-format.md](references/tmt-xml-format.md)
