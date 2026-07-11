import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import ViewerPreviewBar from "@/components/ViewerPreviewBar";
import { getViewerContext } from "@/lib/viewerContext";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "broker-cockpit",
  description: "Unified portfolio cockpit — Robinhood mirror + IBKR execution.",
};

export const viewport: Viewport = {
  themeColor: "#0e0f13",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const { previewing } = await getViewerContext();
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        {previewing && <ViewerPreviewBar />}
        {children}
      </body>
    </html>
  );
}
