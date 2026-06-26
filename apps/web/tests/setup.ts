import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

class MockEventSource {
  static instances: MockEventSource[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

Object.defineProperty(window, "EventSource", {
  value: MockEventSource,
  writable: true
});

Object.assign(globalThis, { MockEventSource });

afterEach(() => {
  cleanup();
  MockEventSource.instances = [];
  document.cookie = "quran_video_editor_state=; Max-Age=0; Path=/";
});
