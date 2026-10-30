// The sandboxed document frame (app/frame/[kind]): a page of our own, loaded into `<iframe sandbox="allow-scripts">`
// (an opaque origin: no cookies, storage or same-origin API), that receives its document by postMessage. Unlike
// srcdoc it carries its own Content-Security-Policy instead of inheriting the Portal's, so a generated or delivered
// page can run its inline scripts while the Portal keeps a strict nonce policy (HLD §6.16).
//
//   doc            a delivered HTML file (decks, reports): as before, it may load what it references.
//   genui-preview  a generated page still being written: its markup shows, no script of its own can run
//                  (the policy admits only the shell, by hash; inline handlers and javascript: URLs are blocked).
//   genui-run      a complete generated page: its scripts run, nothing reaches the network.

export const FRAME_KINDS = ["doc", "genui-preview", "genui-run"] as const;
export type FrameKind = (typeof FRAME_KINDS)[number];

/** Messages a frame sends its host. Only these, and only from the frame's own window, are acted on. */
export type FrameMessage = { ailab: "ready" } | { ailab: "height"; h: number };

/** Waits for one document, then shows it. A preview frame keeps listening and repaints each newer version;
 * the others write it once (document.write replaces the shell, and with it this listener). */
export const SHELL = `(function(){
var kind=document.documentElement.getAttribute("data-kind"),done=false;
function height(){parent.postMessage({ailab:"height",h:Math.ceil(document.documentElement.scrollHeight)},"*")}
addEventListener("message",function(e){
if(e.source!==parent||!e.data||typeof e.data.html!=="string")return;
if(kind==="genui-preview"){document.documentElement.innerHTML=e.data.html;height();return}
if(done)return;done=true;document.open();document.write(e.data.html);document.close()});
if(kind==="genui-preview"&&typeof ResizeObserver!=="undefined")new ResizeObserver(height).observe(document.documentElement);
parent.postMessage({ailab:"ready"},"*")})()`;

/** Added to a complete generated page: reports its height so the frame grows with it, and follows theme changes. */
export const RUN_HELPER = `<script>(function(){
function height(){parent.postMessage({ailab:"height",h:Math.ceil(document.documentElement.scrollHeight)},"*")}
addEventListener("message",function(e){if(e.source!==parent||!e.data||typeof e.data.theme!=="string")return;var s=document.getElementById("ailab-theme");if(s)s.textContent=e.data.theme});
if(typeof ResizeObserver!=="undefined")new ResizeObserver(height).observe(document.documentElement);
addEventListener("load",height);height()})()</script>`;

const NO_NETWORK = "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'";

export function framePolicy(kind: FrameKind, shellHash: string): string {
  if (kind === "genui-preview") return `${NO_NETWORK}; script-src '${shellHash}'`;
  if (kind === "genui-run") return `${NO_NETWORK}; script-src 'unsafe-inline'`;
  // a delivered file keeps what it had as srcdoc: it may reference fonts, images and scripts; it still cannot
  // submit forms or be framed elsewhere
  return "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'; form-action 'none'; base-uri 'self'; frame-ancestors 'self'";
}
