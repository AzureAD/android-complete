# TMT XML Format Reference

## Table of Contents
- [File Structure](#file-structure)
- [Coordinate Rules](#coordinate-rules)
- [Element Types](#element-types)
- [Port System](#port-system)
- [Edge Routing](#edge-routing)
- [Common Gotchas](#common-gotchas)

## File Structure

A .tm7 file is XML with three top-level sections inside `<ThreatModel>`:

1. `<DrawingSurfaceList>` — Contains one `<DrawingSurfaceModel>` per diagram tab
2. `<MetaInformation>` — Counter metadata
3. `<KnowledgeBase>` — Element type definitions and STRIDE threat categories (~400KB)

Each `DrawingSurfaceModel` contains:
- `<Borders>` — Nodes (processes, data stores, external interactors) and trust boundaries
- `<Lines>` — Data flow connectors between nodes

## Coordinate Rules

**CRITICAL**: All coordinates must be >= 10. TMT treats 0 as invalid/corrupted.

| Rule | Value | Consequence of violation |
|------|-------|------------------------|
| Minimum Left/Top | 50 | "Border element coordinates are corrupted" error |
| Minimum HandleX/HandleY | 50 | "Line element coordinates are corrupted" error |
| Maximum canvas width | ~1800px | Elements beyond the grid background area are not visible |
| Maximum canvas height | ~1200px | Elements beyond the grid background area are not visible |
| Boundary padding | >= 60px around children | TMT may auto-correct boundaries |
| Containment | Parent boundary must fully enclose all children | Corruption warning |

**Canvas size**: TMT's drawing surface has a finite visible area defined by the grid-background region (~1800x1200px). Elements placed beyond this area exist in the XML but are not rendered. Keep all nodes, boundaries, and edge handles within these limits.

## Element Types

### GenericTypeId values
| Type | GenericTypeId | StencilType | TypeId |
|------|-------------|-------------|--------|
| Process | `GE.P` | `StencilEllipse` | `SE.P.TMCore.WinApp` |
| Data Store | `GE.DS` | `StencilParallelLines` | `SE.DS.TMCore.Cache` |
| External Interactor | `GE.IE` | `StencilRectangle` | `SE.P.TMCore.BrowserClient` |
| Trust Boundary (border) | `GE.TB.B` | `BorderBoundary` | `SE.TB.B.TMCore.Sandbox` |
| Trust Boundary (line) | `GE.TB.L` | `LineBoundary` | `SE.TB.L.TMCore.Internet` |
| Data Flow (generic/IPC) | `GE.DF` | `Connector` | `SE.DF.TMCore.NamedPipe` |
| Data Flow (HTTPS) | `GE.DF` | `Connector` | `SE.DF.TMCore.HTTPS` |
| Drawing Surface | `DRAWINGSURFACE` | N/A | `DRAWINGSURFACE` |

### Key Properties
- **Out Of Scope**: Property GUID `71f3d9aa-b8ef-4e54-8126-607a1d903103` — skips STRIDE threat generation
- **Name**: Displayed on the element in the diagram

## Port System

Edge endpoints connect to nodes via ports (compass directions):

```
       North
        |
NorthWest -- NorthEast
   |              |
West   [Node]   East
   |              |
SouthWest -- SouthEast
        |
      South
```

Port selection determines where the arrow attaches to the node and how it curves.

## Edge Routing

Edges use `HandleX` and `HandleY` to control the midpoint/label position:
- The handle is the control point for the Bézier curve between source and target
- Moving the handle perpendicular to the source→target line creates an arc
- Moving it along the line shifts where the label appears

### Hand-placing edges (recommended)
Always provide explicit `hx`, `hy`, `portSource`, `portTarget` in the JSON spec. Algorithmic routing produces overlapping labels with complex diagrams.

**Port selection strategy**:
- Use diverse ports for edges from the same node to create visual separation
- For a hub node with 4+ edges, assign: North, NorthEast, East, SouthEast, South, SouthWest, West, NorthWest
- Pair request/response edges on complementary ports (e.g., North/NorthWest for request, East/West for response)

### Spreading parallel edges
When multiple edges connect the same pair of nodes:

1. **Offset handles by ~80–100px** along the axis between the two nodes
2. **Use different port pairs** for each edge (e.g., North→NorthWest, East→West, South→SouthWest)
3. **Never rely on perpendicular offset alone** — opposite-direction edges (A→B vs B→A) with symmetric perpendicular offsets collapse to the same visual curve

Example: 4 edges between Broker(380,310) and eSTS(1100,200):
```
Edge 2: hx=780, hy=85,  ports: North → NorthWest   (top arc)
Edge 3: hx=720, hy=210, ports: NorthWest → NorthEast (upper straight)
Edge 10: hx=810, hy=260, ports: East → West          (middle straight)
Edge 11: hx=750, hy=360, ports: West → East          (lower straight)
```
This gives 4 visually distinct curves spread ~80px apart vertically.

### Self-loops
Use different ports (e.g., South→SouthEast) with handle below the node.

## Common Gotchas

1. **Left=0 causes corruption** — Always use Left >= 50
2. **Boundary ordering** — Inner boundaries should appear before outer boundaries in the XML
3. **z:Id must be unique** — Each element needs a unique `z:Id` attribute
4. **Namespace-heavy** — All position fields require the full `xmlns` declaration
5. **Global string replace is dangerous** — Use GUID-targeted replacements only
6. **Self-loops need distinct ports** — Same source and target port won't render
7. **Line boundaries go in Lines** — `LineBoundary` elements belong in `<Lines>`, not `<Borders>`
8. **Canvas overflow** — Elements beyond the grid background (~1800×1200px) exist in XML but are invisible in TMT
9. **Boundary label position** — TMT renders boundary names near the top-left of the boundary box. Edge handles placed at the same coordinates will overlap the label
10. **Nested boundary spacing** — Leave ≥80px between inner and outer boundary edges so their labels don't collide

## Layout Design Guide

These principles produce clean diagrams matching hand-built TMT quality:

### Node placement
- **Hub-spoke**: Put the node with most connections (usually Broker) at center-left (~x=380, y=310)
- **Cloud nodes to the right**: Place eSTS, DRS, etc. beyond the internet boundary line (~x=1050+)
- **2D spread**: Distribute nodes across both X and Y axes. Never place all nodes in a single row
- **Vertical separation for cloud**: If there are multiple cloud nodes (eSTS, Fed IdP), space them ~400px vertically
- **Keep within 1200×900px**: Practical maximum for readable diagrams

### Boundary design
- **Box boundaries** (`BorderBoundary`): Use for app sandboxes, device boundary
- **Line boundary** (`LineBoundary`): Use for internet/network boundary. A vertical dashed line at ~x=900 is cleaner than a box around cloud nodes
- **Line boundary coordinates**: `x1,y1` = top of line, `x2,y2` = bottom, `hx,hy` = midpoint curve control

### What good looks like (reference: Device-Bound PRT Flow)
```
  [Credential Cache] (120,85)
                |
         [Broker] (393,414)    |  Internet   [AAD] (1399,281)
        /       \              |  Boundary
  [MSAL]         [Content Prov] |  (line)
  (65,703)       (766,346)     |            [DRS] (1403,752)
```
- Hub (Broker) at center with radiating spokes
- Line boundary separates device from cloud
- 100×100 node size
- Each edge hand-placed with unique handle + ports
- ~20 edges, no overlapping labels
