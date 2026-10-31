import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "../components/AuthProvider";
import { ProgressProvider } from "../components/ProgressProvider";
export const metadata: Metadata = { title: "Verba | Speak with confidence", description: "British English speaking practice for educators" };
export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) { return <html lang="en"><body><AuthProvider><ProgressProvider>{children}</ProgressProvider></AuthProvider></body></html>; }
