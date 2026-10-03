import { expect, test } from 'bun:test'
import { readAnswerStream, type AnswerEvent } from './src/stream'

function stream(parts: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({ start(controller) {
    for (const part of parts) controller.enqueue(part)
    controller.close()
  } })
}

test('reads split UTF-8 and newline-delimited answer events', async () => {
  const encoded = new TextEncoder().encode('{"type":"delta","text":"café"}\n{"type":"done","sources":[]}\n')
  const split = encoded.indexOf(0xc3) + 1
  const events: AnswerEvent[] = []
  await readAnswerStream(stream([encoded.slice(0, split), encoded.slice(split, split + 8), encoded.slice(split + 8)]), event => events.push(event))
  expect(events).toEqual([{ type: 'delta', text: 'café' }, { type: 'done', sources: [] }])
})

test('rejects a stream without a completion event', async () => {
  const encoded = new TextEncoder().encode('{"type":"delta","text":"partial"}\n')
  await expect(readAnswerStream(stream([encoded]), () => {})).rejects.toThrow('ended early')
})
