#!/usr/bin/env node
/**
 * Export a threat model spec as Markdown with Mermaid diagram.
 *
 * Usage:
 *   node export_markdown.js --spec spec.json --output threat-model.md [--stride stride.json]
 */

const fs = require("fs");
const { execSync } = require("child_process");
const path = require("path");

function escMd(s) {
  return s.replace(/\|/g, "\\|");
}

function buildMermaid(spec) {
  const lines = ["flowchart TD"];
  const nodes = Object.fromEntries((spec.nodes || []).map((n) => [n.id, n]));

  for (const n of spec.nodes || []) {
    const name = n.name.replace(/"/g, "&quot;");
    if (n.type === "datastore") lines.push(`    ${n.id}[("${name}")]`);
    else if (n.type === "external") lines.push(`    ${n.id}["${name}"]`);
    else lines.push(`    ${n.id}(("${name}"))`);
  }

  for (const e of spec.edges || []) {
    // Use quoted label syntax to handle special chars (?, ://, /, +, parens)
    const label = e.name.replace(/"/g, "&quot;");
    lines.push(`    ${e.from} -- "${label}" --> ${e.to}`);
  }

  // Use sanitized subgraph IDs (no spaces) with display labels
  let sgIdx = 0;
  for (const b of spec.boundaries || []) {
    if (b.type === "line") continue; // skip line boundaries — not representable as subgraphs
    const nodeChildren = (b.contains || []).filter((c) => c in nodes);
    if (nodeChildren.length) {
      const sgId = `sg${sgIdx++}`;
      const label = b.name.replace(/"/g, "&quot;");
      lines.push(`    subgraph ${sgId}["${label}"]`);
      for (const c of nodeChildren) lines.push(`        ${c}`);
      lines.push("    end");
    }
  }
  return lines.join("\n");
}

function buildTable(headers, rows) {
  const sep = headers.map(() => "---").join("|");
  return `|${headers.join("|")}|\n|${sep}|\n` +
    rows.map((r) => `|${r.join("|")}|`).join("\n");
}

function exportMarkdown(specPath, outputPath, stridePath) {
  const spec = JSON.parse(fs.readFileSync(specPath, "utf-8"));
  const nodes = Object.fromEntries((spec.nodes || []).map((n) => [n.id, n.name]));

  // Components table
  const compRows = (spec.nodes || []).map((n) => [
    escMd(n.name), n.type || "process", n.outOfScope ? "No" : "Yes", n.notes || "",
  ]);

  // Boundaries table
  const bndNames = Object.fromEntries((spec.boundaries || []).map((b) => [b.id, b.name]));
  const bndRows = (spec.boundaries || []).map((b) => [
    escMd(b.name), b.type || "border",
    (b.contains || []).map((c) => nodes[c] || bndNames[c] || c).join(", "),
  ]);

  // Data flow table
  const dfRows = (spec.edges || []).map((e, i) => [
    String(i + 1), escMd(nodes[e.from] || e.from), escMd(nodes[e.to] || e.to),
    escMd(e.name), e.type || "generic",
  ]);

  // STRIDE section
  let strideSection = "";
  if (stridePath && fs.existsSync(stridePath)) {
    const stride = JSON.parse(fs.readFileSync(stridePath, "utf-8"));
    const cats = [
      ["spoofing", "Spoofing", "Can an attacker pretend to be something they're not?"],
      ["tampering", "Tampering", "Can an attacker modify data in transit or at rest?"],
      ["repudiation", "Repudiation", "Can an attacker deny performing an action?"],
      ["information_disclosure", "Information Disclosure", "Can an attacker access confidential data?"],
      ["denial_of_service", "Denial of Service", "Can an attacker prevent legitimate use?"],
      ["elevation_of_privilege", "Elevation of Privilege", "Can an attacker gain unauthorized capabilities?"],
    ];
    strideSection = "\n## STRIDE Threat Analysis\n";
    for (const [key, name, desc] of cats) {
      strideSection += `\n### ${name}\n_${desc}_\n\n`;
      const threats = stride[key] || [];
      if (threats.length) {
        strideSection += "|Threat|Data Flow|Mitigation|Status|\n|---|---|---|---|\n";
        for (const t of threats) {
          strideSection += `|${escMd(t.threat)}|${escMd(t.dataflow || "N/A")}|${escMd(t.mitigation || "TODO")}|${t.status || "Open"}|\n`;
        }
      } else {
        strideSection += "_No threats identified._\n";
      }
    }
  }

  // Try to render Mermaid to SVG for portable embedding
  const mermaidCode = buildMermaid(spec);
  const svgName = outputPath.replace(/\.md$/, "-diagram.svg");
  const svgBaseName = svgName.split(/[\\/]/).pop();
  let diagramSection;
  try {
    const tmpMmd = outputPath.replace(/\.md$/, "-diagram.mmd");
    fs.writeFileSync(tmpMmd, mermaidCode, "utf-8");
    // Use quoted paths to prevent injection — inputs are file paths from our own spec
    const quotedMmd = `"${tmpMmd.replace(/"/g, "")}"`;
    const quotedSvg = `"${svgName.replace(/"/g, "")}"`;
    execSync(`npx -y @mermaid-js/mermaid-cli -i ${quotedMmd} -o ${quotedSvg} --quiet`, {
      timeout: 60000,
      stdio: ["ignore", "ignore", "ignore"],
    });
    fs.unlinkSync(tmpMmd);
    // Embed SVG image with mermaid source as collapsible fallback
    diagramSection = `![${spec.title}](${svgBaseName})

<details><summary>Mermaid source</summary>

\`\`\`mermaid
${mermaidCode}
\`\`\`

</details>`;
    console.log(`Created: ${svgName}`);
  } catch {
    // mmdc not available — fall back to mermaid code block only
    diagramSection = `\`\`\`mermaid
${mermaidCode}
\`\`\``;
  }

  const md = `# Threat Model: ${spec.title}

> Auto-generated. To convert to .tm7, use the companion \`threat-model-spec.json\` with \`create_tm7.js\`.

## Diagram

${diagramSection}

## Components

${buildTable(["Component", "Type", "In Scope", "Notes"], compRows)}

## Trust Boundaries

${bndRows.length ? buildTable(["Boundary", "Type", "Contains"], bndRows) : "_No trust boundaries defined._"}

## Data Flows

${buildTable(["#", "From", "To", "Description", "Type"], dfRows)}
${strideSection}`;

  fs.writeFileSync(outputPath, md, "utf-8");

  // Save spec alongside
  const specOut = outputPath.replace(/\.md$/, "-spec.json");
  fs.writeFileSync(specOut, JSON.stringify(spec, null, 2), "utf-8");

  console.log(`Created: ${outputPath}`);
  console.log(`Created: ${specOut} (for .tm7 conversion)`);
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const idx = (flag) => args.indexOf(flag);
  const specPath = args[idx("--spec") + 1];
  const outputPath = args[idx("--output") + 1];
  const strideIdx = idx("--stride");
  const stridePath = strideIdx >= 0 ? args[strideIdx + 1] : null;

  if (!specPath || !outputPath) {
    console.error("Usage: node export_markdown.js --spec spec.json --output out.md [--stride stride.json]");
    process.exit(1);
  }
  exportMarkdown(specPath, outputPath, stridePath);
}
