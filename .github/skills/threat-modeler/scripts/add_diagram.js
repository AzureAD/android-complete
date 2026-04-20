#!/usr/bin/env node
/**
 * Add a new drawing surface to an existing .tm7 file.
 *
 * Usage:
 *   node add_diagram.js --file existing.tm7 --spec spec.json
 */

const fs = require("fs");
const { computeLayout, computeEdgeRouting, buildDrawingSurface } = require("./create_tm7.js");

function addDiagram(specPath, tm7Path) {
  const spec = JSON.parse(fs.readFileSync(specPath, "utf-8"));
  const content = fs.readFileSync(tm7Path, "utf-8");

  const { positions, guids } = computeLayout(spec);
  const routed = computeEdgeRouting(spec, positions, guids);
  const diagramXml = buildDrawingSurface(spec, positions, guids, routed);

  const closeTag = "</DrawingSurfaceList>";
  const idx = content.indexOf(closeTag);
  if (idx < 0) {
    console.error("Error: Could not find </DrawingSurfaceList> in the file.");
    process.exit(1);
  }

  const result = content.slice(0, idx) + diagramXml + content.slice(idx);
  fs.writeFileSync(tm7Path, result, "utf-8");

  console.log(`Added diagram to: ${tm7Path}`);
  console.log(`  Diagram: ${spec.title}`);
  console.log(`  Nodes: ${spec.nodes.length}`);
  console.log(`  Boundaries: ${(spec.boundaries || []).length}`);
  console.log(`  Edges: ${(spec.edges || []).length}`);
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const idx = (flag) => args.indexOf(flag);
  const specPath = args[idx("--spec") + 1];
  const filePath = args[idx("--file") + 1];

  if (!specPath || !filePath) {
    console.error("Usage: node add_diagram.js --file existing.tm7 --spec spec.json");
    process.exit(1);
  }
  addDiagram(specPath, filePath);
}
