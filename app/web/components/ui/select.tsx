/*
 * shadcn/ui "select" styling (MIT, https://ui.shadcn.com) on a native <select>, written by hand.
 * Native on purpose: phones open their own picker (large targets, screen-reader friendly, no portal),
 * and the streamer runs this app on a phone while streaming.
 */
import * as React from "react"
import { ChevronDown } from "lucide-react"

import { cn } from "@/lib/utils"

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  wrapperClassName?: string
}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(({ className, wrapperClassName, children, ...props }, ref) => (
  <div className={cn("relative", wrapperClassName)}>
    <select
      ref={ref}
      className={cn(
        "h-11 w-full appearance-none rounded-md border border-input bg-background py-2 pl-3 pr-9 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {children}
    </select>
    <ChevronDown aria-hidden className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
  </div>
))
Select.displayName = "Select"

export { Select }
