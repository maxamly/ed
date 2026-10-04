import { now } from "@/lib/time"

export function tick() {
  console.log("tick", now())
}
