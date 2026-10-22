import type { ReactNode } from 'react'

export default function LoadField({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-base-content/60">{title}</dt>
      <dd className="mt-1 break-words text-sm font-medium">{children ?? '—'}</dd>
    </div>
  )
}
