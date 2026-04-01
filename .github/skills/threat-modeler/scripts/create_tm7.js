#!/usr/bin/env node
/**
 * Create a new .tm7 file from a JSON threat model spec.
 *
 * Usage:
 *   node create_tm7.js --spec spec.json --output diagram.tm7 --template template.tm7
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

// --- Constants ---
const NS_ABS =
  "http://schemas.datacontract.org/2004/07/ThreatModeling.Model.Abstracts";
const NS_KB =
  "http://schemas.datacontract.org/2004/07/ThreatModeling.KnowledgeBase";
const NS_ARRAYS =
  "http://schemas.microsoft.com/2003/10/Serialization/Arrays";
const NS_SER = "http://schemas.microsoft.com/2003/10/Serialization/";
const NS_XSD = "http://www.w3.org/2001/XMLSchema";
const NS_XSI = "http://www.w3.org/2001/XMLSchema-instance";

const TYPE_IDS = { process: "GE.P", datastore: "GE.DS", external: "GE.IE" };
const STENCIL_TYPES = {
  process: "StencilEllipse",
  datastore: "StencilParallelLines",
  external: "StencilRectangle",
};
const DISPLAY_NAMES = {
  process: "Native Application",
  datastore: "SQL Database",
  external: "Browser",
};
const TYPE_ID_SUFFIXES = {
  process: "SE.P.TMCore.WinApp",
  datastore: "SE.DS.TMCore.Cache",
  external: "SE.P.TMCore.BrowserClient",
};

const MIN_COORD = 50;
const BOUNDARY_PAD = 60;
const MIN_BOUNDARY_GAP = 30; // Minimum gap between nested boundary edges
const NODE_W = 100;  // Match TMT hand-drawn diagrams
const NODE_H = 100;
const MIN_NODE_GAP_X = 350;  // Minimum gap; grows with edge count
const MIN_HANDLE_Y = 80;

// Vertical zones (computed dynamically based on edge count)
// Zone layout from top to bottom:
//   TOP_ARC_ZONE    - long-range forward arcs (well above nodes)
//   UPPER_ZONE      - short-range forward edge labels (above nodes)
//   NODE_ZONE       - node centers
//   LOWER_ZONE      - short-range backward edge labels (below nodes)
//   SELFLOOP_ZONE   - self-loops
//   BOTTOM_ARC_ZONE - long-range backward arcs (well below nodes)

// --- Helpers ---
function newGuid() {
  return crypto.randomUUID();
}

function zId(guid) {
  let h = 0;
  for (const c of guid) h = ((h << 5) - h + c.charCodeAt(0)) | 0;
  return `i${(Math.abs(h) % 9000) + 1000}`;
}

function escXml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// --- XML Builders ---
function buildProcessXml(guid, name, nodeType, outOfScope, l, t, w, h) {
  const stencil = STENCIL_TYPES[nodeType] || "StencilEllipse";
  const typeId = TYPE_IDS[nodeType] || "GE.P";
  const displayName = DISPLAY_NAMES[nodeType] || "Native Application";
  const fullTypeId = TYPE_ID_SUFFIXES[nodeType] || "SE.Ellipse.TMCore.NativeApplication";
  const oos = outOfScope ? "true" : "false";
  const eName = escXml(name);

  return `<a:KeyValueOfguidanyType><a:Key>${guid}</a:Key>` +
    `<a:Value z:Id="${zId(guid)}" i:type="${stencil}" xmlns:z="${NS_SER}">` +
    `<GenericTypeId xmlns="${NS_ABS}">${typeId}</GenericTypeId>` +
    `<Guid xmlns="${NS_ABS}">${guid}</Guid>` +
    `<Properties xmlns="${NS_ABS}">` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>${displayName}</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Name</b:DisplayName><b:Name />` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}">${eName}</b:Value></a:anyType>` +
    `<a:anyType i:type="b:BooleanDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Out Of Scope</b:DisplayName>` +
    `<b:Name>71f3d9aa-b8ef-4e54-8126-607a1d903103</b:Name>` +
    `<b:Value i:type="c:boolean" xmlns:c="${NS_XSD}">${oos}</b:Value></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Reason For Out Of Scope</b:DisplayName>` +
    `<b:Name>752473b6-52d4-4776-9a24-202153f7d579</b:Name>` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}" /></a:anyType>` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Predefined Static Attributes</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `</Properties>` +
    `<TypeId xmlns="${NS_ABS}">${fullTypeId}</TypeId>` +
    `<Height xmlns="${NS_ABS}">${h}</Height>` +
    `<Left xmlns="${NS_ABS}">${l}</Left>` +
    `<StrokeDashArray i:nil="true" xmlns="${NS_ABS}" />` +
    `<StrokeThickness xmlns="${NS_ABS}">1</StrokeThickness>` +
    `<Top xmlns="${NS_ABS}">${t}</Top>` +
    `<Width xmlns="${NS_ABS}">${w}</Width>` +
    `<Fill xmlns="${NS_ABS}">#ffffff</Fill>` +
    `<Stroke xmlns="${NS_ABS}">#000000</Stroke>` +
    `</a:Value></a:KeyValueOfguidanyType>`;
}

function buildBoundaryXml(guid, name, l, t, w, h) {
  const eName = escXml(name);
  return `<a:KeyValueOfguidanyType><a:Key>${guid}</a:Key>` +
    `<a:Value z:Id="${zId(guid)}" i:type="BorderBoundary" xmlns:z="${NS_SER}">` +
    `<GenericTypeId xmlns="${NS_ABS}">GE.TB.B</GenericTypeId>` +
    `<Guid xmlns="${NS_ABS}">${guid}</Guid>` +
    `<Properties xmlns="${NS_ABS}">` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Trust Boundary</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Name</b:DisplayName><b:Name />` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}">${eName}</b:Value></a:anyType>` +
    `<a:anyType i:type="b:BooleanDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Out Of Scope</b:DisplayName>` +
    `<b:Name>71f3d9aa-b8ef-4e54-8126-607a1d903103</b:Name>` +
    `<b:Value i:type="c:boolean" xmlns:c="${NS_XSD}">false</b:Value></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Reason For Out Of Scope</b:DisplayName>` +
    `<b:Name>752473b6-52d4-4776-9a24-202153f7d579</b:Name>` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}" /></a:anyType>` +
    `</Properties>` +
    `<TypeId xmlns="${NS_ABS}">SE.TB.B.TMCore.Sandbox</TypeId>` +
    `<Height xmlns="${NS_ABS}">${h}</Height>` +
    `<Left xmlns="${NS_ABS}">${l}</Left>` +
    `<StrokeDashArray xmlns="${NS_ABS}">4,2</StrokeDashArray>` +
    `<StrokeThickness xmlns="${NS_ABS}">1</StrokeThickness>` +
    `<Top xmlns="${NS_ABS}">${t}</Top>` +
    `<Width xmlns="${NS_ABS}">${w}</Width>` +
    `<Fill xmlns="${NS_ABS}">#00ffffff</Fill>` +
    `<Stroke xmlns="${NS_ABS}">#ff0000</Stroke>` +
    `</a:Value></a:KeyValueOfguidanyType>`;
}

function buildLineBoundaryXml(guid, name, sx, sy, tx, ty, hx, hy) {
  const eName = escXml(name);
  return `<a:KeyValueOfguidanyType><a:Key>${guid}</a:Key>` +
    `<a:Value z:Id="${zId(guid)}" i:type="LineBoundary" xmlns:z="${NS_SER}">` +
    `<GenericTypeId xmlns="${NS_ABS}">GE.TB.L</GenericTypeId>` +
    `<Guid xmlns="${NS_ABS}">${guid}</Guid>` +
    `<Properties xmlns="${NS_ABS}">` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Internet Boundary</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Name</b:DisplayName><b:Name />` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}">${eName}</b:Value></a:anyType>` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Configurable Attributes</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>As Generic Trust Line Boundary</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `</Properties>` +
    `<TypeId xmlns="${NS_ABS}">SE.TB.L.TMCore.Internet</TypeId>` +
    `<HandleX xmlns="${NS_ABS}">${hx}</HandleX>` +
    `<HandleY xmlns="${NS_ABS}">${hy}</HandleY>` +
    `<PortSource xmlns="${NS_ABS}">None</PortSource>` +
    `<PortTarget xmlns="${NS_ABS}">None</PortTarget>` +
    `<SourceGuid xmlns="${NS_ABS}">00000000-0000-0000-0000-000000000000</SourceGuid>` +
    `<SourceX xmlns="${NS_ABS}">${sx}</SourceX>` +
    `<SourceY xmlns="${NS_ABS}">${sy}</SourceY>` +
    `<TargetGuid xmlns="${NS_ABS}">00000000-0000-0000-0000-000000000000</TargetGuid>` +
    `<TargetX xmlns="${NS_ABS}">${tx}</TargetX>` +
    `<TargetY xmlns="${NS_ABS}">${ty}</TargetY>` +
    `</a:Value></a:KeyValueOfguidanyType>`;
}

function buildConnectorXml(guid, name, srcGuid, tgtGuid, hx, hy, pSrc, pTgt) {
  const eName = escXml(name);
  return `<a:KeyValueOfguidanyType><a:Key>${guid}</a:Key>` +
    `<a:Value z:Id="${zId(guid)}" i:type="Connector" xmlns:z="${NS_SER}">` +
    `<GenericTypeId xmlns="${NS_ABS}">GE.DF</GenericTypeId>` +
    `<Guid xmlns="${NS_ABS}">${guid}</Guid>` +
    `<Properties xmlns="${NS_ABS}">` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Named Pipe</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Name</b:DisplayName><b:Name />` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}">${eName}</b:Value></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Dataflow Order</b:DisplayName>` +
    `<b:Name>15ccd509-98eb-49ad-b9c2-b4a2926d1780</b:Name>` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}">0</b:Value></a:anyType>` +
    `<a:anyType i:type="b:BooleanDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Out Of Scope</b:DisplayName>` +
    `<b:Name>71f3d9aa-b8ef-4e54-8126-607a1d903103</b:Name>` +
    `<b:Value i:type="c:boolean" xmlns:c="${NS_XSD}">false</b:Value></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Reason For Out Of Scope</b:DisplayName>` +
    `<b:Name>752473b6-52d4-4776-9a24-202153f7d579</b:Name>` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}" /></a:anyType>` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Configurable Attributes</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>As Generic Data Flow</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `</Properties>` +
    `<TypeId xmlns="${NS_ABS}">SE.DF.TMCore.NamedPipe</TypeId>` +
    `<HandleX xmlns="${NS_ABS}">${hx}</HandleX>` +
    `<HandleY xmlns="${NS_ABS}">${hy}</HandleY>` +
    `<PortSource xmlns="${NS_ABS}">${pSrc}</PortSource>` +
    `<PortTarget xmlns="${NS_ABS}">${pTgt}</PortTarget>` +
    `<SourceGuid xmlns="${NS_ABS}">${srcGuid}</SourceGuid>` +
    `<TargetGuid xmlns="${NS_ABS}">${tgtGuid}</TargetGuid>` +
    `</a:Value></a:KeyValueOfguidanyType>`;
}

/**
 * Ensure parent boundaries have at least MIN_BOUNDARY_GAP pixels of
 * space around child boundaries on all four sides.  Prevents inner
 * boundaries from visually touching their parent's edge — especially
 * when MIN_COORD clamping pushes both to the same coordinate.
 */
function enforceNestingGaps(positions, boundaries) {
  function boundaryDepthG(b) {
    let depth = 0;
    for (const cid of b.contains || []) {
      const child = boundaries.find(x => x.id === cid);
      if (child) depth = Math.max(depth, boundaryDepthG(child) + 1);
    }
    return depth;
  }
  const sorted = [...boundaries].sort((a, b) => boundaryDepthG(a) - boundaryDepthG(b));

  for (const b of sorted) {
    if (b.type === "line") continue;
    const bp = positions[b.id];
    if (!bp) continue;

    for (const cid of b.contains || []) {
      const cp = positions[cid];
      if (!cp || cp.left === undefined) continue;

      // Only enforce gap between parent and child *boundaries*, not nodes
      const isChildBoundary = boundaries.some(x => x.id === cid);
      if (!isChildBoundary) continue;

      const childRight = cp.left + cp.width;
      const childBottom = cp.top + cp.height;

      // Left gap
      const leftGap = cp.left - bp.left;
      if (leftGap < MIN_BOUNDARY_GAP) {
        const needed = MIN_BOUNDARY_GAP - leftGap;
        bp.left -= needed;
        bp.width += needed;
      }

      // Top gap
      const topGap = cp.top - bp.top;
      if (topGap < MIN_BOUNDARY_GAP) {
        const needed = MIN_BOUNDARY_GAP - topGap;
        bp.top -= needed;
        bp.height += needed;
      }

      // Right gap
      const parentRight = bp.left + bp.width;
      if (parentRight - childRight < MIN_BOUNDARY_GAP) {
        bp.width += MIN_BOUNDARY_GAP - (parentRight - childRight);
      }

      // Bottom gap
      const parentBottom = bp.top + bp.height;
      if (parentBottom - childBottom < MIN_BOUNDARY_GAP) {
        bp.height += MIN_BOUNDARY_GAP - (parentBottom - childBottom);
      }
    }
  }
}

// --- Layout Engine ---
function computeLayout(spec) {
  const positions = {};
  const guids = {};
  const nodes = spec.nodes || [];
  const boundaries = spec.boundaries || [];
  const edges = spec.edges || [];

  // Assign GUIDs
  for (const n of nodes) guids[n.id] = newGuid();
  for (const b of boundaries) guids[b.id] = newGuid();

  // --- Explicit positions mode ---
  // If ANY node has x,y, use explicit positioning for all nodes that have it
  const hasExplicit = nodes.some(n => n.x !== undefined && n.y !== undefined);
  if (hasExplicit) {
    const nw = spec.nodeWidth || NODE_W;
    const nh = spec.nodeHeight || NODE_H;
    for (const n of nodes) {
      if (n.x !== undefined && n.y !== undefined) {
        positions[n.id] = { left: n.x, top: n.y, width: nw, height: nh };
      }
    }
    // Compute boundaries from contained children
    const pad = spec.boundaryPad || BOUNDARY_PAD;
    function boundaryDepthE(b) {
      let depth = 0;
      for (const cid of b.contains || []) {
        const child = boundaries.find(x => x.id === cid);
        if (child) depth = Math.max(depth, boundaryDepthE(child) + 1);
      }
      return depth;
    }
    const sortedB = [...boundaries].sort((a, b) => boundaryDepthE(a) - boundaryDepthE(b));
    for (const b of sortedB) {
      if (b.type === "line") continue; // line boundaries don't have rect positions
      const contains = b.contains || [];
      if (!contains.length) continue;
      let minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity;
      for (const cid of contains) {
        const p = positions[cid];
        if (p && p.left !== undefined) {
          minL = Math.min(minL, p.left);
          minT = Math.min(minT, p.top);
          maxR = Math.max(maxR, p.left + p.width);
          maxB = Math.max(maxB, p.top + p.height);
        }
      }
      if (minL === Infinity) continue;
      const idealLeft = Math.round(minL - pad);
      const idealTop = Math.round(minT - pad);
      const left = Math.max(MIN_COORD, idealLeft);
      const top = Math.max(MIN_COORD, idealTop);
      const right = Math.round(maxR + pad);
      const bottom = Math.round(maxB + pad);
      positions[b.id] = {
        left,
        top,
        width: right - left,
        height: bottom - top,
      };
    }
    enforceNestingGaps(positions, boundaries);
    return { positions, guids };
  }

  // --- Auto-layout mode (original 1D row) ---
  // Count edges to determine spacing needs
  const forwardEdges = edges.filter(e => e.from !== e.to);
  const selfLoops = edges.filter(e => e.from === e.to);

  // Count how many edges go above vs below for zone sizing
  let topArcCount = 0, upperCount = 0, lowerCount = 0, bottomCount = 0;
  // We'll do a pre-pass to classify edges
  const nodeIdx = {};
  nodes.forEach((n, i) => { nodeIdx[n.id] = i; });

  for (const e of edges) {
    if (e.from === e.to) { bottomCount++; continue; }
    const si = nodeIdx[e.from] ?? 0;
    const ti = nodeIdx[e.to] ?? 0;
    const span = Math.abs(ti - si);
    if (si < ti) { // forward
      if (span >= 2) topArcCount++;
      else upperCount++;
    } else { // backward
      if (span >= 2) bottomCount++;
      else lowerCount++;
    }
  }

  // Compute vertical zones
  const EDGE_SLOT_HEIGHT = 60; // vertical space per edge slot
  const topArcZoneH = Math.max(80, topArcCount * EDGE_SLOT_HEIGHT);
  const upperZoneH = Math.max(60, upperCount * EDGE_SLOT_HEIGHT);
  const nodeZoneH = NODE_H;
  const lowerZoneH = Math.max(60, lowerCount * EDGE_SLOT_HEIGHT);
  const selfLoopH = Math.max(0, selfLoops.length * EDGE_SLOT_HEIGHT);
  const bottomArcH = Math.max(80, bottomCount * EDGE_SLOT_HEIGHT);

  // Node top Y: enough room for top arcs and upper zone above
  // Add extra padding so top arcs clear boundary labels
  const NODE_TOP = MIN_COORD + topArcZoneH + upperZoneH + BOUNDARY_PAD * 3;

  // Dynamic horizontal gap: wider when there are many edges between adjacent nodes
  const maxEdgesBetweenAdjacent = Math.max(2, upperCount + lowerCount);
  const NODE_GAP_X = Math.max(MIN_NODE_GAP_X, 200 + maxEdgesBetweenAdjacent * 40);

  // Position nodes left-to-right
  const startX = MIN_COORD + BOUNDARY_PAD * 3;
  for (let i = 0; i < nodes.length; i++) {
    positions[nodes[i].id] = {
      left: startX + i * (NODE_W + NODE_GAP_X),
      top: NODE_TOP,
      width: NODE_W,
      height: NODE_H,
    };
  }

  // Store zone info for edge routing
  positions._zones = {
    topArcBaseY: MIN_COORD + topArcZoneH, // top arcs go upward from here
    upperBaseY: NODE_TOP - 20,             // upper labels sit just above nodes
    nodeTopY: NODE_TOP,
    nodeBotY: NODE_TOP + NODE_H,
    lowerBaseY: NODE_TOP + NODE_H + 40,    // lower labels sit just below nodes
    selfLoopBaseY: NODE_TOP + NODE_H + 40 + lowerZoneH,
    bottomArcBaseY: NODE_TOP + NODE_H + 40 + lowerZoneH + selfLoopH,
    edgeSlotH: EDGE_SLOT_HEIGHT,
  };

  // Compute boundary depth (inner = 0, outer = higher)
  function boundaryDepth(b) {
    let depth = 0;
    for (const cid of b.contains || []) {
      const child = boundaries.find((x) => x.id === cid);
      if (child) depth = Math.max(depth, boundaryDepth(child) + 1);
    }
    return depth;
  }

  // Sort inner-first
  const sorted = [...boundaries].sort(
    (a, b) => boundaryDepth(a) - boundaryDepth(b)
  );

  for (const b of sorted) {
    const contains = b.contains || [];
    if (!contains.length) continue;

    let minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity;
    for (const cid of contains) {
      const p = positions[cid];
      if (p && p.left !== undefined) {
        minL = Math.min(minL, p.left);
        minT = Math.min(minT, p.top);
        maxR = Math.max(maxR, p.left + p.width);
        maxB = Math.max(maxB, p.top + p.height);
      }
    }

    if (minL === Infinity) continue; // no positioned children

    const left = Math.max(MIN_COORD, Math.round(minL - BOUNDARY_PAD));
    const top = Math.max(MIN_COORD, Math.round(minT - BOUNDARY_PAD));
    positions[b.id] = {
      left,
      top,
      width: Math.round(maxR - left + BOUNDARY_PAD),
      height: Math.round(maxB - top + BOUNDARY_PAD),
    };
  }

  enforceNestingGaps(positions, boundaries);
  return { positions, guids };
}

// Choose best compass port based on relative position of the other node
function pickPorts(scx, scy, tcx, tcy) {
  const dx = tcx - scx, dy = tcy - scy;
  const angle = Math.atan2(dy, dx) * 180 / Math.PI; // -180..180, 0=right
  function portFor(a) {
    if (a >= -22 && a < 22) return "East";
    if (a >= 22 && a < 67) return "SouthEast";
    if (a >= 67 && a < 112) return "South";
    if (a >= 112 && a < 157) return "SouthWest";
    if (a >= 157 || a < -157) return "West";
    if (a >= -157 && a < -112) return "NorthWest";
    if (a >= -112 && a < -67) return "North";
    return "NorthEast";
  }
  return { pSrc: portFor(angle), pTgt: portFor(angle + 180 > 180 ? angle - 180 : angle + 180) };
}

// Compute the ideal port for a node given its center and a handle position.
// The port should face toward the handle so the Bézier curve exits/enters
// the node in the handle's direction, preventing crossing over the node.
function idealPortForHandle(cx, cy, hx, hy) {
  const dx = hx - cx, dy = hy - cy;
  const angle = Math.atan2(dy, dx) * 180 / Math.PI;
  if (angle >= -22.5 && angle < 22.5) return "East";
  if (angle >= 22.5 && angle < 67.5) return "SouthEast";
  if (angle >= 67.5 && angle < 112.5) return "South";
  if (angle >= 112.5 && angle < 157.5) return "SouthWest";
  if (angle >= 157.5 || angle < -157.5) return "West";
  if (angle >= -157.5 && angle < -112.5) return "NorthWest";
  if (angle >= -112.5 && angle < -67.5) return "North";
  return "NorthEast";
}

// Check if a specified port is within 90° of the ideal direction.
// Ports more than 90° off cause the Bézier to loop over the node.
function portCompatible(specifiedPort, ideal) {
  const portAngles = {
    North: -90, NorthEast: -45, East: 0, SouthEast: 45,
    South: 90, SouthWest: 135, West: 180, NorthWest: -135,
  };
  const a = portAngles[specifiedPort];
  const b = portAngles[ideal];
  if (a === undefined || b === undefined) return true;
  let diff = Math.abs(a - b);
  if (diff > 180) diff = 360 - diff;
  return diff <= 90;
}

// Validate and auto-correct ports on hand-placed edges.
// If a port faces away from the handle (>90° off), replace it with
// the ideal port to prevent the curve from crossing over the node.
function validatePorts(edges, positions) {
  let corrected = 0;
  for (const e of edges) {
    if (e.handleX === undefined || e.handleY === undefined) continue;
    const sp = positions[e.sourceId || e.from];
    const tp = positions[e.targetId || e.to];
    if (!sp || !tp) continue;
    const scx = sp.left + sp.width / 2, scy = sp.top + sp.height / 2;
    const tcx = tp.left + tp.width / 2, tcy = tp.top + tp.height / 2;
    const idealSrc = idealPortForHandle(scx, scy, e.handleX, e.handleY);
    const idealTgt = idealPortForHandle(tcx, tcy, e.handleX, e.handleY);
    if (e.portSource && !portCompatible(e.portSource, idealSrc)) {
      e.portSource = idealSrc;
      corrected++;
    }
    if (e.portTarget && !portCompatible(e.portTarget, idealTgt)) {
      e.portTarget = idealTgt;
      corrected++;
    }
  }
  return corrected;
}

// Detect handle clustering: when multiple edges connected to the same node
// have handles approaching from the same angular sector (~45° wedge).
// This causes arrow overlap at hub nodes. Logs warnings so the AI/user
// can redistribute handles around the node.
function detectHandleClustering(edges, positions) {
  const portAngles = {
    North: -90, NorthEast: -45, East: 0, SouthEast: 45,
    South: 90, SouthWest: 135, West: 180, NorthWest: -135,
  };

  // Group edges by which node they touch and which port they use on that node
  const nodePortUsage = {}; // nodeId -> { portName -> [edgeName, ...] }

  for (const e of edges) {
    const srcId = e.sourceId || e.from;
    const tgtId = e.targetId || e.to;
    if (e.portSource) {
      (nodePortUsage[srcId] = nodePortUsage[srcId] || {})[e.portSource] =
        (nodePortUsage[srcId]?.[e.portSource] || []);
      nodePortUsage[srcId][e.portSource].push(e.name);
    }
    if (e.portTarget) {
      (nodePortUsage[tgtId] = nodePortUsage[tgtId] || {})[e.portTarget] =
        (nodePortUsage[tgtId]?.[e.portTarget] || []);
      nodePortUsage[tgtId][e.portTarget].push(e.name);
    }
  }

  // Also check angular proximity of handles relative to node center
  const nodeHandleAngles = {}; // nodeId -> [{angle, name}, ...]
  for (const e of edges) {
    if (e.handleX === undefined || e.handleY === undefined) continue;
    for (const nodeId of [e.sourceId || e.from, e.targetId || e.to]) {
      const p = positions[nodeId];
      if (!p) continue;
      const cx = p.left + p.width / 2, cy = p.top + p.height / 2;
      const angle = Math.atan2(e.handleY - cy, e.handleX - cx) * 180 / Math.PI;
      (nodeHandleAngles[nodeId] = nodeHandleAngles[nodeId] || []).push({
        angle, name: e.name,
      });
    }
  }

  const warnings = [];

  // Warn if same port used by 2+ edges on the same node
  for (const [nodeId, ports] of Object.entries(nodePortUsage)) {
    for (const [port, edgeNames] of Object.entries(ports)) {
      if (edgeNames.length >= 2) {
        warnings.push(
          `Node "${nodeId}": port ${port} used by ${edgeNames.length} edges (${edgeNames.join(", ")}). ` +
          `Distribute to different ports to prevent overlap.`
        );
      }
    }
  }

  // Warn if 3+ handles cluster within a 90° sector on any node
  for (const [nodeId, angles] of Object.entries(nodeHandleAngles)) {
    if (angles.length < 3) continue;
    angles.sort((a, b) => a.angle - b.angle);
    for (let i = 0; i < angles.length; i++) {
      let count = 1;
      const clustered = [angles[i].name];
      for (let j = 1; j < angles.length; j++) {
        const idx = (i + j) % angles.length;
        let diff = angles[idx].angle - angles[i].angle;
        if (diff < 0) diff += 360;
        if (diff <= 90) {
          count++;
          clustered.push(angles[idx].name);
        }
      }
      if (count >= 3) {
        warnings.push(
          `Node "${nodeId}": ${count} edge handles within a 90° sector (${clustered.join(", ")}). ` +
          `Fan handles around the node to prevent visual overlap.`
        );
        break; // One warning per node is enough
      }
    }
  }

  return warnings;
}

function computeEdgeRouting(spec, positions, guids) {
  const routed = [];
  const zones = positions._zones || {};
  const nodes = spec.nodes || [];
  const nodeIdx = {};
  nodes.forEach((n, i) => { nodeIdx[n.id] = i; });

  // Check if we're in 2D (explicit) mode
  const is2D = nodes.some(n => n.x !== undefined && n.y !== undefined);

  if (is2D) {
    // --- 2D edge routing ---
    // If edge has explicit hx/hy, use them directly.
    // Otherwise, auto-route with grid-based offset per pair.
    const edges = spec.edges || [];
    const SLOT_SPACING = 75;

    // Check if ALL edges have explicit handles (hand-placed mode)
    const allExplicit = edges.every(e => e.hx !== undefined && e.hy !== undefined);

    if (allExplicit) {
      // Hand-placed mode: use spec values with port validation
      // Build validation-friendly array, validate, then emit
      const validationEdges = edges.map(e => {
        const sp = positions[e.from], tp = positions[e.to];
        if (!sp || !tp) return null;
        const scx = sp.left + sp.width / 2, scy = sp.top + sp.height / 2;
        const tcx = tp.left + tp.width / 2, tcy = tp.top + tp.height / 2;
        const { pSrc, pTgt } = e.portSource
          ? { pSrc: e.portSource, pTgt: e.portTarget }
          : pickPorts(scx, scy, tcx, tcy);
        return {
          from: e.from, to: e.to, name: e.name,
          sourceId: e.from, targetId: e.to,
          handleX: e.hx, handleY: e.hy,
          portSource: pSrc, portTarget: pTgt,
          sourceGuid: guids[e.from], targetGuid: guids[e.to],
        };
      }).filter(Boolean);

      const corrected = validatePorts(validationEdges, positions);
      if (corrected > 0) {
        console.log(`  Port validation: auto-corrected ${corrected} port(s) to prevent edge crossings`);
      }

      // Detect handle clustering (multiple edges approaching same node from same direction)
      const clusterWarnings = detectHandleClustering(validationEdges, positions);
      for (const w of clusterWarnings) {
        console.log(`  WARNING: ${w}`);
      }

      for (const ve of validationEdges) {
        routed.push({
          guid: newGuid(), name: ve.name,
          sourceGuid: ve.sourceGuid, targetGuid: ve.targetGuid,
          handleX: ve.handleX, handleY: ve.handleY,
          portSource: ve.portSource, portTarget: ve.portTarget,
        });
      }
      return routed;
    }

    // Auto-route: grid-based offset per undirected pair
    const pairEdges = {};
    edges.forEach((e, i) => {
      const pairKey = [e.from, e.to].sort().join("|");
      (pairEdges[pairKey] = pairEdges[pairKey] || []).push(i);
    });
    const routeResult = new Array(edges.length);
    for (const [pairKey, indices] of Object.entries(pairEdges)) {
      const nodeA = positions[pairKey.split("|")[0]];
      const nodeB = positions[pairKey.split("|")[1]];
      if (!nodeA || !nodeB) continue;
      const ax = nodeA.left + nodeA.width / 2, ay = nodeA.top + nodeA.height / 2;
      const bx = nodeB.left + nodeB.width / 2, by = nodeB.top + nodeB.height / 2;
      const dx = bx - ax, dy = by - ay;
      const len = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / len, uy = dy / len;
      const px = -uy, py = ux;
      const midX = (ax + bx) / 2, midY = (ay + by) / 2;
      const n = indices.length;
      for (let s = 0; s < n; s++) {
        const eIdx = indices[s];
        const e = edges[eIdx];
        let hx, hy;
        if (e.hx !== undefined && e.hy !== undefined) {
          hx = e.hx; hy = e.hy;
        } else {
          let alongShift, perpShift;
          if (n <= 2) {
            alongShift = (s - (n - 1) / 2) * SLOT_SPACING;
            perpShift = 0;
          } else {
            const col = s % 2, row = Math.floor(s / 2);
            alongShift = (col - 0.5) * SLOT_SPACING;
            perpShift = (row - (Math.ceil(n / 2) - 1) / 2) * SLOT_SPACING;
          }
          hx = Math.max(MIN_HANDLE_Y, Math.round(midX + ux * alongShift + px * perpShift));
          hy = Math.max(MIN_HANDLE_Y, Math.round(midY + uy * alongShift + py * perpShift));
        }
        const sp = positions[e.from], tp = positions[e.to];
        const scx = sp.left + sp.width / 2, scy = sp.top + sp.height / 2;
        const tcx = tp.left + tp.width / 2, tcy = tp.top + tp.height / 2;
        const { pSrc, pTgt } = e.portSource
          ? { pSrc: e.portSource, pTgt: e.portTarget }
          : pickPorts(scx, scy, tcx, tcy);
        routeResult[eIdx] = {
          guid: newGuid(), name: e.name,
          sourceGuid: guids[e.from], targetGuid: guids[e.to],
          handleX: hx, handleY: hy, portSource: pSrc, portTarget: pTgt,
        };
      }
    }
    return routeResult.filter(Boolean);
  }

  // --- 1D zone-based routing (original) ---
  let topArcSlot = 0, upperSlot = 0, lowerSlot = 0, selfLoopSlot = 0, bottomArcSlot = 0;

  for (const e of spec.edges || []) {
    const sp = positions[e.from];
    const tp = positions[e.to];
    if (!sp || !tp || sp.left === undefined || tp.left === undefined) continue;

    const scx = sp.left + sp.width / 2;
    const scy = sp.top + sp.height / 2;
    const tcx = tp.left + tp.width / 2;
    const midX = Math.round((scx + tcx) / 2);

    const si = nodeIdx[e.from] ?? 0;
    const ti = nodeIdx[e.to] ?? 0;
    const span = Math.abs(ti - si);

    let hx, hy, pSrc, pTgt;

    if (e.from === e.to) {
      hx = Math.round(scx);
      hy = Math.round(zones.selfLoopBaseY + selfLoopSlot * zones.edgeSlotH);
      pSrc = "South"; pTgt = "SouthEast";
      selfLoopSlot++;
    } else if (si < ti) {
      if (span >= 2) {
        hx = midX;
        hy = Math.max(MIN_HANDLE_Y, Math.round(zones.topArcBaseY - topArcSlot * zones.edgeSlotH));
        pSrc = "North"; pTgt = "NorthWest";
        topArcSlot++;
      } else {
        hx = midX;
        hy = Math.max(MIN_HANDLE_Y, Math.round(zones.upperBaseY - upperSlot * zones.edgeSlotH));
        pSrc = "NorthEast"; pTgt = "NorthWest";
        upperSlot++;
      }
    } else {
      if (span >= 2) {
        hx = midX;
        hy = Math.round(zones.bottomArcBaseY + bottomArcSlot * zones.edgeSlotH);
        pSrc = "SouthWest"; pTgt = "SouthEast";
        bottomArcSlot++;
      } else {
        hx = midX;
        hy = Math.round(zones.lowerBaseY + lowerSlot * zones.edgeSlotH);
        pSrc = "SouthWest"; pTgt = "SouthEast";
        lowerSlot++;
      }
    }

    routed.push({
      guid: newGuid(), name: e.name,
      sourceGuid: guids[e.from], targetGuid: guids[e.to],
      handleX: hx, handleY: hy, portSource: pSrc, portTarget: pTgt,
    });
  }
  return routed;
}

// --- Drawing Surface Builder ---
function buildDrawingSurface(spec, positions, guids, routedEdges) {
  const dsGuid = newGuid();
  const title = escXml(spec.title);
  const boundaries = spec.boundaries || [];

  function boundaryDepth(b) {
    let depth = 0;
    for (const cid of b.contains || []) {
      const child = boundaries.find((x) => x.id === cid);
      if (child) depth = Math.max(depth, boundaryDepth(child) + 1);
    }
    return depth;
  }

  let xml =
    `<DrawingSurfaceModel z:Id="${zId(dsGuid)}" xmlns:z="${NS_SER}">` +
    `<GenericTypeId xmlns="${NS_ABS}">DRAWINGSURFACE</GenericTypeId>` +
    `<Guid xmlns="${NS_ABS}">${dsGuid}</Guid>` +
    `<Properties xmlns="${NS_ABS}" xmlns:a="${NS_ARRAYS}">` +
    `<a:anyType i:type="b:HeaderDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Diagram</b:DisplayName><b:Name /><b:Value i:nil="true" /></a:anyType>` +
    `<a:anyType i:type="b:StringDisplayAttribute" xmlns:b="${NS_KB}">` +
    `<b:DisplayName>Name</b:DisplayName><b:Name />` +
    `<b:Value i:type="c:string" xmlns:c="${NS_XSD}">${title}</b:Value></a:anyType>` +
    `</Properties>` +
    `<TypeId xmlns="${NS_ABS}">DRAWINGSURFACE</TypeId>`;

  // Borders
  xml += `<Borders xmlns:a="${NS_ARRAYS}">`;
  for (const n of spec.nodes) {
    const p = positions[n.id];
    xml += buildProcessXml(
      guids[n.id], n.name, n.type || "process",
      !!n.outOfScope, p.left, p.top, p.width, p.height
    );
  }
  for (const b of [...boundaries].sort((a, c) => boundaryDepth(a) - boundaryDepth(c))) {
    const p = positions[b.id];
    if (p) xml += buildBoundaryXml(guids[b.id], b.name, p.left, p.top, p.width, p.height);
  }
  xml += "</Borders>";

  // Lines (edges + line boundaries)
  xml += `<Lines xmlns:a="${NS_ARRAYS}">`;
  for (const e of routedEdges) {
    xml += buildConnectorXml(
      e.guid, e.name, e.sourceGuid, e.targetGuid,
      e.handleX, e.handleY, e.portSource, e.portTarget
    );
  }
  // Line boundaries go in Lines section (not Borders)
  for (const b of boundaries.filter(b => b.type === "line")) {
    const lb = b.line || {};
    xml += buildLineBoundaryXml(
      guids[b.id], b.name,
      lb.x1 || 900, lb.y1 || 50, lb.x2 || 900, lb.y2 || 900,
      lb.hx || 850, lb.hy || 450
    );
  }
  xml += "</Lines>";

  xml += "</DrawingSurfaceModel>";
  return xml;
}

// --- Main ---
function createTm7(specPath, outputPath, templatePath) {
  const spec = JSON.parse(fs.readFileSync(specPath, "utf-8"));
  const template = fs.readFileSync(templatePath, "utf-8");

  const { positions, guids } = computeLayout(spec);
  const routed = computeEdgeRouting(spec, positions, guids);
  const diagramXml = buildDrawingSurface(spec, positions, guids, routed);

  const oldDsl = "<DrawingSurfaceList />";
  const newDsl = `<DrawingSurfaceList>${diagramXml}</DrawingSurfaceList>`;

  let result;
  if (template.includes(oldDsl)) {
    result = template.replace(oldDsl, newDsl);
  } else {
    const idx = template.indexOf("</DrawingSurfaceList>");
    result = template.slice(0, idx) + diagramXml + template.slice(idx);
  }

  fs.writeFileSync(outputPath, result, "utf-8");
  console.log(`Created: ${outputPath}`);
  console.log(`  Diagram: ${spec.title}`);
  console.log(`  Nodes: ${spec.nodes.length}`);
  console.log(`  Boundaries: ${(spec.boundaries || []).length}`);
  console.log(`  Edges: ${(spec.edges || []).length}`);
}

// Exports for add_diagram.js
module.exports = { computeLayout, computeEdgeRouting, buildDrawingSurface };

// CLI
if (require.main === module) {
  const args = process.argv.slice(2);
  const idx = (flag) => args.indexOf(flag);
  const specPath = args[idx("--spec") + 1];
  const outputPath = args[idx("--output") + 1];
  const templatePath = args[idx("--template") + 1];

  if (!specPath || !outputPath || !templatePath) {
    console.error("Usage: node create_tm7.js --spec spec.json --output out.tm7 --template template.tm7");
    process.exit(1);
  }
  createTm7(specPath, outputPath, templatePath);
}
