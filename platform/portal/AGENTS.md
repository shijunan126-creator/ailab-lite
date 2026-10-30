<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Portal rules (ADR 0027 / 0028)

- CSS cascade: `app/globals.css` orders `theme < base (preflight) < components < utilities`; all Portal stylesheets are in components and load base, shell, chat, library, pages, app, genui. Colors only through tokens (`styles/tokens.css`, no legacy aliases); dark mode is the `.dark` class, never `prefers-color-scheme` in component CSS. Class names built from templates (`tone-${kind}`) look unused to a grep: search for the prefix before deleting a rule.
- CSP: `proxy.ts` sends a nonce policy for every page; inline scripts in `app/layout.tsx` carry the nonce. Never render untrusted or generated HTML with `srcdoc` (it inherits the Portal's policy): use `components/sandbox-frame.tsx` and `app/frame/[kind]`.
- E2E selectors depend on existing class names and Chinese labels (`.composer-box`, `.send-btn`, `.turn`, `.attach-chip`, `.skill-tag`, `aria-label="发送"`/`"添加附件"`…). Rebuilding a part keeps those hooks or updates `platform/e2e` in the same change. Skills are `menuitemradio` items in the composer's + menu (`更多技能` is a `menuitem`; use `e2e/test_portal.py::_pick_skill`). The 对话/工作 radios exist only before a conversation starts (home hero, `.composer-mode` on a project page); an open conversation has no mode switch — fork to change it.
- Font sizes only through `--text-*` tokens. In Tailwind write `text-(length:--text-sm)`: `text-[var(--text-sm)]` is parsed as a colour and silently sets no size.
- Streaming: SSE events go through `lib/stream/batcher.ts`; the reducer in `lib/work-process.ts` must keep unchanged items by reference (tested). Callbacks passed to `Turn` go through `useStable`.
- Sending goes through `lib/outbox.ts` (one path for the composer and GenUI buttons); attachments through `lib/uploads.ts`. Do not await uploads in the composer.
- GenUI: catalog in `components/genui/catalog/schema.ts`. After changing it run `npm run genui:prompt` and commit the regenerated `ailab_platform/contracts/genui/*`; the drift test fails otherwise. Never call `library.prompt()` with the generic inline mode.
- `npm test` is Vitest. Old `node:test` files use `tests/node-test-compat.mjs`; component tests add `// @vitest-environment jsdom`.
