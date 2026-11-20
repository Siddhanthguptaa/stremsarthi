/*
 * Adapted from Saximn/privastream (Apache-2.0), src/web/frontend/lib/utils.ts. See LICENSE.privastream.
 */
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
