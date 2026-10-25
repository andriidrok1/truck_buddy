"""Intent accuracy: gpt-5.4-mini vs gpt-5.5 on 20 driver phrases (run manually, hits the real LLM)."""
import asyncio, sys, time
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from models import AgentRequest, Place, TruckProfile
from services import agent

NO_CTX = "Driver location: 38.58160, -121.49440. No route is active."
ROUTE_CTX = ("Driver location: 38.58160, -121.49440. Conversation so far: Driver: find me a truck stop | "
             "Copilot: Route set to Capitol Truck Plaza on I-80 Exit 85, 4.7 miles away.. Places found earlier: "
             "1=Capitol Truck Plaza (truck_stop) 4.7 mi [current route]; 2=Sierra Fuel & Truck Stop (truck_stop) 5.2 mi; "
             "3=Delta Junction Truck Stop (truck_stop) 6.0 mi. Current route: Capitol Truck Plaza "
             "(I-80 Exit 85, West Sacramento), 4.7 miles, about 9 minutes.")
CASES = [
    ("I'm tired", NO_CTX, lambda d: d.get("intent") == "find" and d.get("category") == "hotel"),
    ("I need fuel", NO_CTX, lambda d: d.get("intent") == "find" and d.get("category") == "truck_stop"),
    ("Need diesel and DEF", NO_CTX, lambda d: d.get("intent") == "find" and d.get("category") == "truck_stop"),
    ("Find me a place to sleep tonight", NO_CTX, lambda d: d.get("intent") == "find" and d.get("category") == "hotel"),
    ("I need a quick break to stretch", NO_CTX, lambda d: d.get("intent") == "find" and d.get("category") == "rest_area"),
    ("Where can I use a restroom", NO_CTX, lambda d: d.get("intent") == "find" and d.get("category") == "rest_area"),
    ("I'm hungry", NO_CTX, lambda d: d.get("intent") == "find" and d.get("category") == "truck_stop"),
    ("Take me to Reno", NO_CTX, lambda d: d.get("intent") == "go" and "reno" in str(d.get("query")).lower()),
    ("Go to 1200 Main Street Dallas Texas", NO_CTX, lambda d: d.get("intent") == "go" and "dallas" in str(d.get("query")).lower()),
    ("I need to get to the Port of Oakland", NO_CTX, lambda d: d.get("intent") == "go" and "oakland" in str(d.get("query")).lower()),
    ("How far is it?", ROUTE_CTX, lambda d: d.get("intent") == "chat" and "4.7" in str(d.get("reply"))),
    ("How long until I get there?", ROUTE_CTX, lambda d: d.get("intent") == "chat" and "9" in str(d.get("reply"))),
    ("Not that one", ROUTE_CTX, lambda d: (d.get("intent") == "pick" and d.get("place_index") in (2, 3)) or (d.get("intent") == "find" and d.get("category") == "truck_stop")),
    ("The second one", ROUTE_CTX, lambda d: d.get("intent") == "pick" and d.get("place_index") == 2),
    ("Take me to the Delta one instead", ROUTE_CTX, lambda d: d.get("intent") == "pick" and d.get("place_index") == 3),
    ("Bye", ROUTE_CTX, lambda d: d.get("intent") == "bye"),
    ("That's all, thanks", ROUTE_CTX, lambda d: d.get("intent") == "bye"),
    ("No, I'm good", ROUTE_CTX, lambda d: d.get("intent") == "bye"),
    ("Beeping", NO_CTX, lambda d: d.get("intent") == "unclear"),
    ("Thanks, man", ROUTE_CTX, lambda d: d.get("intent") in ("chat", "bye") and bool(d.get("reply"))),
]


# Real Google place ids (27 chars): the context is built by the agent itself and the index is resolved server-side.
GOOGLE = [
    Place(id="ChIJd8BlQ2BZwokRAFUEcm_qrcA", name="Pilot Travel Center", category="truck_stop", lat=38.61, lng=-121.54, distance_mi=4.7),
    Place(id="ChIJw____96GhYARCVVwg5cT7c0", name="Love's Travel Stop", category="truck_stop", lat=38.55, lng=-121.41, distance_mi=5.2),
    Place(id="ChIJ7cv00DwsDogRAMDACa2m4K8", name="Delta Junction Truck Stop", category="truck_stop", lat=38.50, lng=-121.51, distance_mi=6.0),
]
GOOGLE_CASES = [("The second one", GOOGLE[1].id), ("Take me to the Delta one instead", GOOGLE[2].id)]


async def run_google() -> tuple[int, list]:
    sess = {"history": ["Driver: find me a truck stop", "Copilot: Route set to Pilot Travel Center, 4.7 miles away."],
            "places": GOOGLE}
    req = AgentRequest(text="", lat=38.5816, lng=-121.4944, active_place=GOOGLE[0], active_distance_mi=4.7, active_duration_min=9)
    ctx = agent.LlmAgentProvider()._context(req, sess)
    ok, fails = 0, []
    for text, want in GOOGLE_CASES:
        d = await agent.classify(text, ctx, TruckProfile())
        p = agent.pick_place(d, GOOGLE)
        if d.get("intent") == "pick" and p and p.id == want:
            ok += 1
        else:
            fails.append((text, d))
    return ok, fails


async def run(model: str):
    agent.MODEL = ("openai", model)
    ok, times, fails = 0, [], []
    for text, ctx, check in CASES:
        t0 = time.perf_counter()
        d = await agent.classify(text, ctx, TruckProfile())
        times.append((time.perf_counter() - t0) * 1000)
        if check(d):
            ok += 1
        else:
            fails.append((text, d))
    g_ok, g_fails = await run_google()
    ok += g_ok
    fails += g_fails
    times.sort()
    print(f"{model}: {ok}/{len(CASES) + len(GOOGLE_CASES)} correct, median {times[len(times)//2]:.0f} ms, max {times[-1]:.0f} ms")
    for f in fails:
        print("   FAIL", f)


async def main():
    for m in sys.argv[1:] or ["gpt-5.4-mini", "gpt-5.5"]:
        await run(m)

asyncio.run(main())
