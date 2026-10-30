import { backAction, type BackState } from "../utils/backAction";

const base: BackState = { micCard: false, routesOpen: false, phase: "ready", hasRoute: false, driving: false };

describe("backAction", () => {
  test("nothing open: the system handles Back", () => {
    expect(backAction(base)).toBeNull();
  });

  test("top layer first: mic card, then routes sheet, then the turn, then the list", () => {
    expect(backAction({ ...base, micCard: true, routesOpen: true, phase: "options", hasRoute: true })).toBe("closeMicCard");
    expect(backAction({ ...base, routesOpen: true, phase: "options", hasRoute: true })).toBe("closeRoutes");
    expect(backAction({ ...base, phase: "listening", hasRoute: true })).toBe("cancelTurn");
    expect(backAction({ ...base, phase: "thinking", hasRoute: true, driving: true })).toBe("cancelTurn");
    expect(backAction({ ...base, phase: "options", hasRoute: true })).toBe("closeOptions");
    expect(backAction({ ...base, phase: "providers" })).toBe("closeOptions");
  });

  test("route preview ends the route like its button; driving ignores Back", () => {
    expect(backAction({ ...base, phase: "navigating", hasRoute: true })).toBe("endRoute");
    expect(backAction({ ...base, phase: "navigating", hasRoute: true, driving: true })).toBe("ignore");
  });
});
