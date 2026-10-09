import { describe, expect, it } from "vitest";
import { contextFor, isKnownRoute, normalize, searchArticles, type HelpArticle } from "./search";

const A = (id: string, title: string, keywords: string[], body = "", contexts = ["general"]): HelpArticle => ({ id, title, keywords, body, contexts, links: [] });
const ARTICLES = [
  A("cancel", "Cancel your subscription", ["cancel", "cancellation", "unsubscribe"], "Open Settings, Billing."),
  A("plans", "Free, Nexis Plus and Nexis Pro", ["plans", "pricing", "difference"], "Monthly prices in AED.", ["billing"]),
  A("pulse", "How Nexis Pulse works", ["pulse", "discussion"], "Discuss assets."),
];

describe("help search", () => {
  it("normalises text", () => expect(normalize("  Hello, WORLD!! ")).toBe("hello world"));
  it("ranks a keyword phrase match first", () => expect(searchArticles(ARTICLES, "how do I cancel my subscription")[0].id).toBe("cancel"));
  it("returns nothing for gibberish", () => expect(searchArticles(ARTICLES, "zzqx qqwv")).toEqual([]));
  it("page context breaks ties", () => expect(searchArticles(ARTICLES, "difference", "billing")[0].id).toBe("plans"));
  it("maps routes to page categories without reading anything else from the URL", () => {
    expect(contextFor("/pulse/d/abc")).toBe("pulse");
    expect(contextFor("/settings", "#plan")).toBe("billing");
    expect(contextFor("/settings")).toBe("account");
    expect(contextFor("/somewhere")).toBe("general");
  });
  it("only allows known internal routes", () => {
    expect(isKnownRoute("/settings#plan")).toBe(true);
    expect(isKnownRoute("https://evil.example")).toBe(false);
    expect(isKnownRoute("/admin")).toBe(false);
  });
});
