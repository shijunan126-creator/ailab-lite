import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Class names joined, later Tailwind utilities winning over earlier ones. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
