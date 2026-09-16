import type { Metadata } from 'next'
import './globals.css'
import './responsive.css'

export const metadata: Metadata = { title: 'MorphoJudge · 闪蝶判官', description: 'AI 生成软件可信审计工作台' }

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>
}
