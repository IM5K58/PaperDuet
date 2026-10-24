import { test, expect } from '@playwright/test';
import { readEvents } from '../src/sse';

test('SSE: split UTF-8, CRLF, multiline data and early cancellation',async()=>{
  const bytes=new TextEncoder().encode(': heartbeat\r\n\r\ndata: {"text":"한글 😀"}\r\n\r\nevent: update\ndata: {"text":\ndata: "next"}\n\n');
  const response=new Response(new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(Uint8Array.of(byte));controller.close();}}));
  const events=[];for await(const event of readEvents(response))events.push(event);
  expect(events).toEqual([{text:'한글 😀'},{text:'next'}]);
  let cancelled=false;
  const pending=new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: {"event":"start"}\n\n'));},cancel(){cancelled=true;}}));
  for await(const event of readEvents(pending)){expect(event).toEqual({event:'start'});break;}
  expect(cancelled).toBe(true);
  expect(pending.body!.locked).toBe(false);
});
