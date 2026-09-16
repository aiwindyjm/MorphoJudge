import type { ReactNode, SVGProps } from 'react'

// 统一线性图标：24px 网格、1.75px 描边、圆角端点、currentColor 继承。
// 与 §11.8 图标规范一致；新增图标必须沿用同一描边与网格。
const shapes: Record<string, ReactNode> = {
  filePlus: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M12 12v5" /><path d="M9.5 14.5h5" /></>,
  trace: <><circle cx="5" cy="19" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="5" r="2" /><path d="M6.6 17.4l3.8-3.8" /><path d="M13.6 10.4l3.8-3.8" /></>,
  graph: <><circle cx="6" cy="7" r="2.5" /><circle cx="18" cy="7" r="2.5" /><circle cx="12" cy="17.5" r="2.5" /><path d="M8.5 7h7" /><path d="M7.7 9.2l3 5.3" /><path d="M16.3 9.2l-3 5.3" /></>,
  methods: <><path d="M9 6h11" /><path d="M9 12h11" /><path d="M9 18h11" /><path d="M4.5 6v.01" /><path d="M4.5 12v.01" /><path d="M4.5 18v.01" /></>,
  comments: <><path d="M8.5 7.5L4.5 12l4 4.5" /><path d="M15.5 7.5l4 4.5-4 4.5" /></>,
  report: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6" /><path d="M9 17h4" /></>,
  folder: <path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4.2l2 2.5H19A1.5 1.5 0 0 1 20.5 9.5V17A1.5 1.5 0 0 1 19 18.5H5A1.5 1.5 0 0 1 3.5 17z" />,
  globe: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17" /><path d="M12 3.5c2.6 2.3 4.2 5.3 4.2 8.5s-1.6 6.2-4.2 8.5c-2.6-2.3-4.2-5.3-4.2-8.5s1.6-6.2 4.2-8.5z" /></>,
  terminal: <><path d="M5.5 5h13a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" /><path d="M7.5 9.5l3 2.5-3 2.5" /><path d="M13.5 15h3.5" /></>,
  package: <><path d="M12 3.2l7.5 4.2v9.2L12 20.8l-7.5-4.2V7.4z" /><path d="M12 12l7.5-4.6" /><path d="M12 12L4.5 7.4" /><path d="M12 12v8.8" /></>,
  download: <><path d="M12 4v11" /><path d="M7.5 11l4.5 4.5L16.5 11" /><path d="M5 19.5h14" /></>,
  cpu: <><path d="M8.5 8.5h7a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-5a1 1 0 0 1 1-1z" /><path d="M9 3.5v3M15 3.5v3M9 17.5v3M15 17.5v3M3.5 9h3M3.5 15h3M17.5 9h3M17.5 15h3" /></>,
}

export type IconName = keyof typeof shapes & string

export function Icon({ name, size = 16, ...rest }: { name: IconName; size?: number } & Omit<SVGProps<SVGSVGElement>, 'name'>) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...rest}>{shapes[name]}</svg>
}
