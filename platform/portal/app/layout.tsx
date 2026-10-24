import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AILab",
  description: "AILab：会思考、能动手的 AI 工作台",
};

// resizes-content: on Android the keyboard shrinks the layout instead of covering the composer
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", interactiveWidget: "resizes-content" };

const SIDEBAR_STATE = `try{if(innerWidth<760||localStorage.getItem("ailab.sidebar")==="closed")document.documentElement.dataset.sidebar="closed"}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        {/* before the first paint: a sidebar the user closed must not appear and then slide away */}
        <script dangerouslySetInnerHTML={{ __html: SIDEBAR_STATE }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
