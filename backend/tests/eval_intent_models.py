"""Intent accuracy: gpt-5.4-mini vs gpt-5.5 on 20 driver phrases (run manually, hits the real LLM)."""
import asyncio, sys, time
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from models import TruckProfile
from services import agent

NO_CTX = "Driver location: 38.58160, -121.49440. No route is active."
ROUTE_CTX = ("Driver location: 38.58160, -121.49440. Conversation so far: Driver: find me a truck stop | "
             "Copilot: Route set to Capitol Truck Plaza on I-80 Exit 85, 4.7 miles away.. Places found earlier: "
             "ts-capitol=Capitol Truck Plaza (truck_stop) 4.7 mi; ts-sierra=Sierra Fuel & Truck Stop (truck_stop) 5.2 mi; "
             "ts-delta=Delta Junction Truck Stop (truck_stop) 6.0 mi. Current route: id ts-capitol, Capitol Truck Plaza "
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
    ("Not that one", ROUTE_CTX, lambda d: (d.get("intent") == "pick" and d.get("place_id") in ("ts-sierra", "ts-delta")) or (d.get("intent") == "find" and d.get("category") == "truck_stop")),
    ("The second one", ROUTE_CTX, lambda d: d.get("intent") == "pick" and d.get("place_id") == "ts-sierra"),
    ("Take me to the Delta one instead", ROUTE_CTX, lambda d: d.get("intent") == "pick" and d.get("place_id") == "ts-delta"),
    ("Bye", ROUTE_CTX, lambda d: d.get("intent") == "bye"),
    ("That's all, thanks", ROUTE_CTX, lambda d: d.get("intent") == "bye"),
    ("No, I'm good", ROUTE_CTX, lambda d: d.get("intent") == "bye"),
    ("Beeping", NO_CTX, lambda d: d.get("intent") == "unclear"),
    ("Thanks, man", ROUTE_CTX, lambda d: d.get("intent") in ("chat", "bye") and bool(d.get("reply"))),
]


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
    times.sort()
    print(f"{model}: {ok}/{len(CASES)} correct, median {times[len(times)//2]:.0f} ms, max {times[-1]:.0f} ms")
    for f in fails:
        print("   FAIL", f)


async def main():
    for m in sys.argv[1:] or ["gpt-5.4-mini", "gpt-5.5"]:
        await run(m)

asyncio.run(main())
