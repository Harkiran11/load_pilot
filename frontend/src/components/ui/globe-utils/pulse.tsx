import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export function Pulse({ className, ...props }: ComponentProps<'span'>) {
  return <span aria-hidden="true" className={cn('relative inline-flex size-3 shrink-0 text-primary', className)} {...props}>
    <span className="absolute inset-0 animate-ping rounded-full bg-current opacity-30 motion-reduce:animate-none" />
    <span className="relative size-full rounded-full bg-current" />
  </span>
}
