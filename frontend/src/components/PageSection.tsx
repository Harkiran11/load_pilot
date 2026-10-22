import type { ReactNode } from 'react'

type PageSectionProps = {
  title: string
  description: string
  children: ReactNode
}

export default function PageSection({ title, children }: PageSectionProps) {
  return (
    <section aria-label={title} className="app-page space-y-6">
      {children}
    </section>
  )
}
