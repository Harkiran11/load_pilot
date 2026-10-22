import * as React from 'react'
import { TooltipTrigger as RadixTooltipTrigger } from '@/components/ui/tooltip'

// The supplied globe demo uses `render`; Radix uses `asChild` for that slot.
const TooltipTrigger = React.forwardRef<
  React.ComponentRef<typeof RadixTooltipTrigger>,
  React.ComponentPropsWithoutRef<typeof RadixTooltipTrigger> & { render?: React.ReactElement }
>(({ render, children, asChild, ...props }, ref) => (
  <RadixTooltipTrigger ref={ref} asChild={render ? true : asChild} {...props}>
    {render ?? children}
  </RadixTooltipTrigger>
))
TooltipTrigger.displayName = 'GlobeTooltipTrigger'

export { TooltipTrigger }
export { Tooltip, TooltipContent, TooltipProvider } from '@/components/ui/tooltip'
