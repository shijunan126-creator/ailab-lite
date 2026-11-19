import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import "./globals.css";
import DialogHost from "../components/dialog-host";
import { PHONE_QUERY } from "../lib/viewport";

export const metadata: Metadata = {
  // a route's own title reads "技能 · AILab"; pages named by their content (a conversation, a project) set it themselves
  title: { default: "AILab", template: "%s · AILab" },
  description: "AILab：会思考、能动手的 AI 工作台",
};

// resizes-content: on Android the keyboard shrinks the layout instead of covering the composer
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", interactiveWidget: "resizes-content" };

const SIDEBAR_STATE = `try{if(matchMedia(${JSON.stringify(PHONE_QUERY)}).matches||localStorage.getItem("ailab.sidebar")==="closed")document.documentElement.dataset.sidebar="closed"}catch(e){}`;
// The theme follows the system and is applied before the first paint, so a dark page never flashes light.
const THEME = `(function(){var q=matchMedia("(prefers-color-scheme: dark)"),r=document.documentElement;function s(){r.classList.toggle("dark",q.matches)}s();q.addEventListener("change",s)})()`;

// Async for the nonce proxy.ts puts on the request: it makes every page render per request, which a nonce needs.
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        {/* before the first paint: a sidebar the user closed must not appear and then slide away */}
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: SIDEBAR_STATE }} />
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: THEME }} />
      </head>
      <body>
        {children}
        <DialogHost />
      </body>
    </html>
  );
}
