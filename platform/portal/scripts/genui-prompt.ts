// npm run genui:prompt — writes the GenUI contract the Agent loads (design 2026-10-30-genui-registry-v2 §3): the
// index for the system prompt, the guide and the per-component docs. Never edit those files by hand.
import { createHash } from "node:crypto";
import { readdirSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { buildCatalog, buildGuide, buildIndex } from "../components/genui/catalog/prompt.ts";
import { CATALOG_VERSION } from "../components/genui/catalog/schema.ts";

const dir = fileURLToPath(new URL("../../ailab_platform/contracts/genui/", import.meta.url));
const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");
const index = buildIndex();
const guide = buildGuide();
const catalog = JSON.stringify(buildCatalog(sha(index), sha(guide)), null, 2) + "\n";
// files of an earlier catalog version would be loadable by mistake
for (const name of readdirSync(dir)) if (/^(catalog-v\d+\.json|openui-v\d+\..+\.md)$/.test(name) && !name.includes(`v${CATALOG_VERSION}`)) rmSync(dir + name);
writeFileSync(`${dir}openui-v${CATALOG_VERSION}.index.md`, index);
writeFileSync(`${dir}openui-v${CATALOG_VERSION}.guide.md`, guide);
writeFileSync(`${dir}catalog-v${CATALOG_VERSION}.json`, catalog);
console.log(`index ${index.length} chars, guide ${guide.length} chars, catalog ${catalog.length} chars`);
