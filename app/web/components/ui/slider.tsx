/*
 * shadcn/ui "slider" (MIT, https://ui.shadcn.com), written by hand from the shadcn source pattern on top
 * of @radix-ui/react-slider. The thumb is 28 px (touch friendly) and takes the accessible name, because
 * Radix puts keyboard focus and the slider role on the thumb, not on the root.
 */
"use client"

import * as React from "react"
import * as SliderPrimitive from "@radix-ui/react-slider"

import { cn } from "@/lib/utils"

export interface SliderProps extends React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root> {
  /** Accessible name of the thumb (translated by the caller). */
  thumbLabel: string
  /** Human readable value, for example "60 %". */
  valueText?: string
}

const Slider = React.forwardRef<React.ElementRef<typeof SliderPrimitive.Root>, SliderProps>(
  ({ className, thumbLabel, valueText, ...props }, ref) => (
    <SliderPrimitive.Root
      ref={ref}
      className={cn("relative flex h-7 w-full touch-none select-none items-center", className)}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-secondary ring-1 ring-input">
        <SliderPrimitive.Range className="absolute h-full bg-primary" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        aria-label={thumbLabel}
        aria-valuetext={valueText}
        className="block size-7 rounded-full border-2 border-primary bg-background shadow transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50"
      />
    </SliderPrimitive.Root>
  ),
)
Slider.displayName = SliderPrimitive.Root.displayName

export { Slider }
