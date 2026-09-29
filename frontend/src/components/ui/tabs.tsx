"use client"

import * as React from "react"
import * as TabsPrimitive from "@radix-ui/react-tabs"

import { cn } from "@/lib/utils"
import glassStyles from "./GlassLens.module.css"
import selectableStyles from "./GlassSelectable.module.css"

function Tabs({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-2", className)}
      {...props}
    />
  )
}

function TabsList({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        "inline-flex h-9 w-fit items-center justify-center rounded-lg border border-white/50 bg-background/25 p-1 text-muted-foreground shadow-[inset_0_1px_0_rgba(255,255,255,0.6)] backdrop-blur-sm dark:border-white/15 dark:bg-white/[0.04] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]",
        className
      )}
      {...props}
    />
  )
}

function TabsTrigger({
  className,
  children,
  onPointerMove,
  onPointerLeave,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        selectableStyles.control,
        "inline-flex items-center justify-center gap-2 rounded-md px-2 py-1 text-sm font-medium whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:font-semibold data-[state=active]:text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      {...props}
      onPointerMove={(event) => {
        onPointerMove?.(event)
        const bounds = event.currentTarget.getBoundingClientRect()
        event.currentTarget.style.setProperty("--glint-x", `${event.clientX - bounds.left}px`)
        event.currentTarget.style.setProperty("--glint-y", `${event.clientY - bounds.top}px`)
      }}
      onPointerLeave={(event) => {
        onPointerLeave?.(event)
        event.currentTarget.style.removeProperty("--glint-x")
        event.currentTarget.style.removeProperty("--glint-y")
      }}
    >
      <span className={cn(glassStyles.lens, selectableStyles.lens)} aria-hidden="true" />
      <span className={cn(selectableStyles.content, "inline-flex items-center gap-2")}>{children}</span>
    </TabsPrimitive.Trigger>
  )
}

function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn("flex-1 outline-none", className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent }
