export type SseEvent = { event: string; data: unknown };

/**
 * Split a text buffer into complete Server-Sent Events.
 * Network chunks can end in the middle of an event, so the unfinished tail is returned in `rest`
 * and must be prepended to the next chunk.
 */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: SseEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (data) events.push({ event, data: JSON.parse(data) });
  }
  return { events, rest };
}
