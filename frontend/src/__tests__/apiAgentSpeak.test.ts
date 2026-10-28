import { api, speedForAgent } from "../api";

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

  test("typed text sends stt_ms and stt_confidence as null", async () => {
    const fetchMock = jest.fn(async () => jsonResponse({ status: "chat", reply: "ok" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await api.agentSpeak("hello", { lat: 0, lng: 0 });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.stt_ms).toBeNull();
    expect(body.stt_confidence).toBeNull();
  });

  test("forwards the STT confidence as stt_confidence", async () => {
    const fetchMock = jest.fn(async () => jsonResponse({ status: "chat", reply: "ok" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await api.agentSpeak("take me to reno", { lat: 0, lng: 0 }, null, undefined, "s1", undefined, null, null, 700, 0.42);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.stt_ms).toBe(700);
    expect(body.stt_confidence).toBe(0.42);
  });

  const speak = async (speed: number | null) => {
    const fetchMock = jest.fn(async () => jsonResponse({ status: "chat", reply: "ok" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await api.agentSpeak("hello", { lat: 0, lng: 0 }, null, undefined, "s1", undefined, null, null, null, null, speed);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    return JSON.parse(init.body as string) as Record<string, unknown>;
  };

  test("sends the GPS speed as speed_mps to one decimal", async () => {
    const body = await speak(12.34);
    expect(body.speed_mps).toBe(12.3);
  });

  test("leaves speed_mps out when the GPS has no speed", async () => {
    for (const speed of [null, -1, NaN, Infinity]) {
      const body = await speak(speed);
      expect(body).not.toHaveProperty("speed_mps");
    }
  });

  test("caps speed_mps at 60 (the backend's upper bound)", async () => {
    expect((await speak(71.9)).speed_mps).toBe(60);
    expect((await speak(0)).speed_mps).toBe(0);
  });
});

describe("speedForAgent", () => {
  test("one decimal, 0..60, undefined when unknown", () => {
    expect(speedForAgent(12.34)).toBe(12.3);
    expect(speedForAgent(12.35)).toBe(12.4);
    expect(speedForAgent(0)).toBe(0);
    expect(speedForAgent(60)).toBe(60);
    expect(speedForAgent(60.04)).toBe(60);
    expect(speedForAgent(200)).toBe(60);
    expect(speedForAgent(-0.5)).toBeUndefined();
    expect(speedForAgent(null)).toBeUndefined();
    expect(speedForAgent(undefined)).toBeUndefined();
    expect(speedForAgent(NaN)).toBeUndefined();
  });
});

describe("api.nudges", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  test("posts the drive context and returns the nudges", async () => {
    const nudge = { kind: "break", text: "You've been driving 7 hours. Want a stop at Pilot Fernley?", priority: 2, offer: "add_stop" };
    const fetchMock = jest.fn(async () => jsonResponse({ nudges: [nudge] }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const out = await api.nudges({
      lat: 39.5,
      lng: -119.8,
      driving: true,
      active_place: null,
      ahead: [[39.6, -119.3, 5.2]],
      drive_elapsed_min: 61,
      local_hour: 14,
      session_id: "dlg-1",
    });
    expect(out.nudges).toEqual([nudge]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/nudges$/);
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.ahead).toEqual([[39.6, -119.3, 5.2]]);
    expect(body.drive_elapsed_min).toBe(61);
    expect(body.local_hour).toBe(14);
    expect(body.session_id).toBe("dlg-1");
    expect(body.truck_profile).toBeDefined(); // added by the client, like /agent
  });
});

jest.mock("../utils/upload", () => ({ uploadForm: jest.fn() }));

describe("api.transcribe", () => {
  const { uploadForm } = jest.requireMock<{ uploadForm: jest.Mock }>("../utils/upload");
  afterEach(() => {
    jest.useRealTimers();
  });

  test("stt_ms is the server's STT stage when reported; confidence null when absent", async () => {
    uploadForm.mockResolvedValueOnce(jsonResponse({ transcript: "truck stop", timings: { stt: 640, upload: 90 } }));
    const out = await api.transcribe("file:///speech.m4a");
    expect(out).toEqual({ transcript: "truck stop", timings: { stt: 640, upload: 90 }, stt_ms: 640, stt_confidence: null });
    // No hint: no field (older backends reject unknown form fields with 422 on some stacks).
    const opts = uploadForm.mock.calls[uploadForm.mock.calls.length - 1][1] as { fields: Record<string, string> };
    expect(opts.fields).toEqual({});
  });

  test("sends the hint field and returns the confidence", async () => {
    uploadForm.mockResolvedValueOnce(jsonResponse({ transcript: "reno", timings: { stt: 500 }, stt_confidence: 0.83 }));
    const out = await api.transcribe("file:///speech.m4a", undefined, "Reno NV, Pilot Fernley, I-80 E");
    expect(out.transcript).toBe("reno");
    expect(out.stt_confidence).toBe(0.83);
    const [url, opts] = uploadForm.mock.calls[uploadForm.mock.calls.length - 1] as [string, { fields: Record<string, string> }];
    expect(url).toMatch(/\/api\/voice\/transcribe$/);
    expect(opts.fields).toEqual({ hint: "Reno NV, Pilot Fernley, I-80 E" });
  });

  test("the hint never passes the server's 600-char cap", async () => {
    uploadForm.mockResolvedValueOnce(jsonResponse({ transcript: "x" }));
    await api.transcribe("file:///speech.m4a", undefined, "a".repeat(700));
    const [, opts] = uploadForm.mock.calls[uploadForm.mock.calls.length - 1] as [string, { fields: Record<string, string> }];
    expect(opts.fields.hint).toHaveLength(600);
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
