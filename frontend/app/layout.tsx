import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Operator",
  description: "Autonomous AI operator: plans, acts, recovers and verifies across your apps",
};

const themeBoot = `try{var t=localStorage.getItem('op-theme');if(t==='dark'||(!t&&matchMedia('(prefers-color-scheme: dark)').matches))document.documentElement.classList.add('dark')}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBoot }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
