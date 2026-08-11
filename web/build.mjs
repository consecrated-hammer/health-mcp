import { build } from "esbuild";
import { mkdir, writeFile } from "node:fs/promises";

const entries = ["today", "checkin", "food-log"];
await mkdir("dist", { recursive: true });

for (const entry of entries) {
  const result = await build({
    entryPoints: [`src/${entry}.ts`],
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2022",
    write: false,
    outdir: "dist",
    minify: true,
    loader: { ".css": "css" },
  });
  const script = result.outputFiles.find((file) => file.path.endsWith(".js"));
  const styles = result.outputFiles.find((file) => file.path.endsWith(".css"));
  if (!script || !styles) throw new Error(`Missing build output for ${entry}`);
  const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${styles.text}</style>
  </head>
  <body>
    <main id="app" aria-live="polite"><p class="loading">Loading health data…</p></main>
    <script type="module">${script.text}</script>
  </body>
</html>`;
  await writeFile(`dist/${entry}.html`, html, "utf8");
}
