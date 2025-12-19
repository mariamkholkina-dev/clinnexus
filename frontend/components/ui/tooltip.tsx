"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

export interface TooltipContextValue {
  open: boolean
  setOpen: (open: boolean) => void
}

const TooltipContext = React.createContext<TooltipContextValue | undefined>(undefined)

export interface TooltipProviderProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}

const TooltipProvider = React.forwardRef<HTMLDivElement, TooltipProviderProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div ref={ref} className={cn("", className)} {...props}>
        <TooltipContext.Provider value={{ open: false, setOpen: () => {} }}>
          {children}
        </TooltipContext.Provider>
      </div>
    )
  }
)
TooltipProvider.displayName = "TooltipProvider"

export interface TooltipProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}

const Tooltip = ({ children }: TooltipProps) => {
  return <>{children}</>
}

export interface TooltipTriggerProps extends React.HTMLAttributes<HTMLElement> {
  asChild?: boolean
}

const TooltipTrigger = React.forwardRef<HTMLElement, TooltipTriggerProps>(
  ({ asChild, children, ...props }, ref) => {
    if (asChild && React.isValidElement(children)) {
      return React.cloneElement(children, { ...props, ref } as any)
    }
    return (
      <span ref={ref as any} {...props}>
        {children}
      </span>
    )
  }
)
TooltipTrigger.displayName = "TooltipTrigger"

export interface TooltipContentProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}

const TooltipContent = React.forwardRef<HTMLDivElement, TooltipContentProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          "z-50 overflow-hidden rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95",
          className
        )}
        {...props}
      >
        {children}
      </div>
    )
  }
)
TooltipContent.displayName = "TooltipContent"

export { TooltipProvider, Tooltip, TooltipTrigger, TooltipContent }

