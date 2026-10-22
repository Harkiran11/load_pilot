import { Globe, GlobeSvg } from '@/components/ui/globe'
import { Pulse } from '@/components/ui/globe-utils/pulse'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/globe-utils/tooltip'
import { InfoIcon } from 'lucide-react'

// Standalone visual example; the dashboard uses live terminals instead of these sample values.
export function GlobeDemo() {
  return <TooltipProvider>
    <div className="flex h-screen w-full items-center justify-center">
      <Globe>
        <GlobeSvg aria-label="Example globe locations">
          <foreignObject x={200} y={180} width={20} height={20}>
            <Tooltip>
              <TooltipTrigger render={<button type="button" aria-label="USA Kansas" className="flex size-full items-center justify-center"><Pulse /></button>} />
              <TooltipContent className="flex-col pb-2">
                <div className="flex items-center justify-between gap-2"><p className="text-muted-foreground">USA Kansas</p><p className="font-medium tabular-nums">$121,000</p></div>
                <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-1"><InfoIcon className="text-destructive" size={16} /><p className="text-xs text-destructive/80">only 8 left</p></div></div>
              </TooltipContent>
            </Tooltip>
          </foreignObject>
          <foreignObject x={600} y={80} width={40} height={40}>
            <Tooltip>
              <TooltipTrigger render={<button type="button" aria-label="Europe France" className="flex size-full items-center justify-center"><Pulse /></button>} />
              <TooltipContent className="flex-col pb-2">
                <div className="flex items-center justify-between gap-2"><p className="text-muted-foreground">Europe France</p><p className="font-medium tabular-nums">$121,000</p></div>
                <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-1"><InfoIcon className="text-yellow-300" size={16} /><p className="text-xs text-yellow-300/80">15 left</p></div></div>
              </TooltipContent>
            </Tooltip>
          </foreignObject>
        </GlobeSvg>
      </Globe>
    </div>
  </TooltipProvider>
}

export default GlobeDemo
