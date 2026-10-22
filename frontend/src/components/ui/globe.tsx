"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { SVG_GLOBE_PATH } from "@/components/ui/globe-utils/svg-globe-path";
import { SVG_GLOBE_BORDERS } from "@/components/ui/globe-utils/svg-globe-borders";

interface GlobePinProps extends React.ComponentProps<"g"> {
  x: number;
  y: number;
}

export function GlobeGlow({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("absolute inset-0 rounded-full blur-3xl pointer-events-none", className)} {...props} />;
}

export function GlobeSvg({ className, children, textureScale = 1, ...props }: React.ComponentProps<"svg"> & { textureScale?: number }) {
  const id = `globe-${React.useId().replace(/:/g, "")}`;
  const scale = Number.isFinite(textureScale) && textureScale > 0 ? textureScale : 1;
  return (
    <svg viewBox="0 0 1259 477" className={cn("size-full", className)} preserveAspectRatio="xMidYMid meet" {...props}>
      <defs>
        <path id={`${id}-land`} d={SVG_GLOBE_PATH.p15b69c00} fillRule="evenodd" clipRule="evenodd" vectorEffect="non-scaling-stroke" />
        <clipPath id={`${id}-clip`}><use href={`#${id}-land`} /></clipPath>
        <pattern id={`${id}-texture`} patternUnits="userSpaceOnUse" width={6 * scale} height={6 * scale}>
          <circle cx={3 * scale} cy={3 * scale} r={0.6 * scale} fill="var(--color-base-content)" opacity="0.13" />
        </pattern>
      </defs>
      <g aria-hidden="true" pointerEvents="none">
        <use href={`#${id}-land`} className="globe-land" />
        <g clipPath={`url(#${id}-clip)`}>
          <rect width="1259" height="477" fill={`url(#${id}-texture)`} />
          <path d={SVG_GLOBE_BORDERS.countries} className="globe-country-borders" vectorEffect="non-scaling-stroke" />
          <path d={SVG_GLOBE_BORDERS.regions} className="globe-region-borders" vectorEffect="non-scaling-stroke" />
        </g>
      </g>
      {children}
    </svg>
  );
}

export function Globe({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("relative flex aspect-2/1 w-full items-center justify-center", className)} {...props} />;
}

export function GlobePin({ x, y, ...props }: GlobePinProps) {
  return (
    <g {...props}>
      <circle cx={x} cy={y} r="4" className="fill-primary" />
      <circle cx={x} cy={y} r="12" className="fill-primary/20 animate-pulse motion-reduce:animate-none" />
    </g>
  );
}

export default Globe;
