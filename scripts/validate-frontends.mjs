import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const files = [
  "frontend/index.html",
  "frontend/teacher.html",
  "frontend/evaluation-teacher.html",
  "frontend/evaluation-student.html",
  "frontend/scorm-editor.html",
  "frontend/deep-link.html",
];

let failed = false;
for (const rel of files) {
  const full = path.join(root, rel);
  if (!fs.existsSync(full)) {
    console.error(`Falta ${rel}`);
    failed = true;
    continue;
  }
  const html = fs.readFileSync(full, "utf8");
  if (!/<html\s+lang=["']es["']/.test(html)) {
    console.error(`${rel}: falta lang="es"`);
    failed = true;
  }
  if (!/name=["']viewport["']/.test(html)) {
    console.error(`${rel}: falta viewport`);
    failed = true;
  }

  const htmlMarkup = html.replace(/<script(?:\s[^>]*)?>[\s\S]*?<\/script>/gi, "");
  const ids = [...htmlMarkup.matchAll(/\sid=["']([^"']+)["']/g)].map(m => m[1]);
  const seen = new Set();
  for (const id of ids) {
    if (seen.has(id)) {
      console.error(`${rel}: id duplicado: ${id}`);
      failed = true;
    }
    seen.add(id);
  }

  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map(m => m[1])
    .filter(Boolean);
  for (let i = 0; i < scripts.length; i++) {
    try {
      new Function(scripts[i]);
    } catch (error) {
      console.error(`${rel}: JavaScript inválido en script ${i + 1}: ${error.message}`);
      failed = true;
    }
  }

  const literalSelectors = [
    ...html.matchAll(/querySelector\(["']#([A-Za-z][A-Za-z0-9_-]*)["']\)/g),
    ...html.matchAll(/getElementById\(["']([A-Za-z][A-Za-z0-9_-]*)["']\)/g),
  ].map(m => m[1]);
  for (const id of new Set(literalSelectors)) {
    if (!seen.has(id)) {
      console.error(`${rel}: JavaScript referencia #${id}, pero ese id no existe`);
      failed = true;
    }
  }
}

if (failed) process.exit(1);
console.log("Frontends principales: sintaxis e identificadores correctos.");
