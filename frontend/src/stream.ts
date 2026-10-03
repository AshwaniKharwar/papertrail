export type AnswerEvent =
  | { type: 'delta'; text: string }
  | { type: 'done'; sources: { page: number; snippet: string }[] }
  | { type: 'error'; message: string }

export async function readAnswerStream(body: ReadableStream<Uint8Array>, onEvent: (event: AnswerEvent) => void) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let newline: number
    while ((newline = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newline)
      buffer = buffer.slice(newline + 1)
      if (!line) continue
      const event = JSON.parse(line) as AnswerEvent
      if (event.type === 'error') throw new Error(event.message)
      if (event.type === 'done') completed = true
      onEvent(event)
    }
  }
  if (!completed) throw new Error('The answer stream ended early. Please try again.')
}
