import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tynato Monitoring",
  description: "Independent service health and incident monitoring for Tynato CRM",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
