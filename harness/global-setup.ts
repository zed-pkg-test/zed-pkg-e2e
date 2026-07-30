import { requireStack } from "./stack.js";

/** Refuse to start a browser at all if the stack under test isn't up: a missing
 *  server otherwise surfaces as a dozen identical navigation timeouts. */
export default async function globalSetup(): Promise<void> {
  await requireStack();
}
