// Copies pdf.js runtime assets (worker, character maps, standard fonts, image decoders, colour
// profiles) into public/pdfjs, so the viewer works offline and always matches the installed
// pdf.js version. Runs before `next dev` and `next build`.
import { cpSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Resolve pdfjs-dist the way react-pdf does, so the worker version matches the library.
const pdfjs = path.dirname(
  require.resolve("pdfjs-dist/package.json", { paths: [path.join(web, "node_modules", "react-pdf")] }),
);
const target = path.join(web, "public", "pdfjs");

rmSync(target, { recursive: true, force: true });
cpSync(path.join(pdfjs, "build", "pdf.worker.min.mjs"), path.join(target, "pdf.worker.min.mjs"));
for (const dir of ["cmaps", "standard_fonts", "wasm", "iccs"]) {
  cpSync(path.join(pdfjs, dir), path.join(target, dir), { recursive: true });
}
console.log(`pdf.js ${require(path.join(pdfjs, "package.json")).version} assets -> public/pdfjs`);
