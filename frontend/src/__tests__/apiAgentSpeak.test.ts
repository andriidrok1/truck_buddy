import { api } from "../api";

const jsonResponse = (body: unknown) => ({
  ok: true,
  status: 200,
  headers: { get: (k: string) => (k.toLowerCase() === "content-type" ? "application/json" : null) },
  json: async () => body,
});

describe("api.agentSpeak", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  test("forwards the STT time as stt_ms", async () => {
    const fetchMock = jest.fn(async () => jsonResponse({ status: "chat", reply: "ok" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await api.agentSpeak("find parking", { lat: 37.7, lng: -122.4 }, null, undefined, "s1", undefined, null, null, 812);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/agent\?speak=true$/);
    const body = JSON.parse(init.body as string);
    expect(body.stt_ms).toBe(812);
    expect(body.text).toBe("find parking");
    expect(body.session_id).toBe("s1");
  });

  test("typed text sends stt_ms as null", async () => {
    const fetchMock = jest.fn(async () => jsonResponse({ status: "chat", reply: "ok" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await api.agentSpeak("hello", { lat: 0, lng: 0 });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string).stt_ms).toBeNull();
  });
});

jest.mock("../utils/upload", () => ({ uploadForm: jest.fn() }));

describe("api.transcribe", () => {
  const { uploadForm } = jest.requireMock<{ uploadForm: jest.Mock }>("../utils/upload");
  afterEach(() => {
    jest.useRealTimers();
  });

  test("stt_ms is the server's STT stage when reported", async () => {
    uploadForm.mockResolvedValueOnce(jsonResponse({ transcript: "truck stop", timings: { stt: 640, upload: 90 } }));
    const out = await api.transcribe("file:///speech.m4a");
    expect(out).toEqual({ transcript: "truck stop", timings: { stt: 640, upload: 90 }, stt_ms: 640 });
  });

  test("stt_ms falls back to the measured wall time", async () => {
    jest.useFakeTimers({ now: 5_000_000 });
    uploadForm.mockImplementationOnce(async () => {
      jest.setSystemTime(5_000_000 + 1234);
      return jsonResponse({ transcript: "hello" });
    });
    const out = await api.transcribe("file:///speech.m4a");
    expect(out.stt_ms).toBe(1234);
  });
});
