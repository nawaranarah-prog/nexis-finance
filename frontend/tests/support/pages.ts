/** The pages the brief asks every suite to cover, with what proves each one loaded. Pages that don't exist yet
    (About) are listed anyway: they render the app's 404 page until they're built, and the baseline says so. */
export type TestPage = { name: string; path: string; ready: string };

export const PULSE_STORY = "/pulse/d/n368jc46bg/us-stocks-how-much-good-news-is-already-priced-in";
export const POST = "/finstagram/p/2353";

export const PAGES: TestPage[] = [
  { name: "home", path: "/", ready: "main h1" },
  { name: "markets", path: "/markets", ready: "main h1" },
  { name: "bonds", path: "/markets?list=uae_bonds", ready: "main h1" },
  { name: "ticker-emaar", path: "/markets/EMAAR.AE", ready: "main h1" },
  { name: "valuation-emaar", path: "/valuation/EMAAR.AE", ready: "main h1" },
  { name: "bond", path: "/markets/UAE0734USD.BOND", ready: "main h1" },
  { name: "compare", path: "/compare", ready: "main h1" },
  { name: "world-graph", path: "/world-graph", ready: "main h1" },
  { name: "time-machine", path: "/time-machine", ready: "main h1" },
  { name: "devils-advocate", path: "/devils-advocate", ready: "main h1" },
  { name: "my-research", path: "/my-research", ready: "main h1" },
  { name: "assistant", path: "/advisor", ready: "main h1, main textarea" },
  { name: "pulse", path: "/pulse", ready: "main h1" },
  { name: "pulse-story", path: PULSE_STORY, ready: "main h1" },
  { name: "finstagram", path: "/finstagram", ready: "main" },
  { name: "post", path: POST, ready: "main" },
  { name: "login", path: "/login", ready: "form" },
  { name: "forgot-password", path: "/forgot-password", ready: "main h1" },
  { name: "verify-email", path: "/verify-email", ready: "main h1" },
  { name: "about", path: "/about", ready: "main h1" },
  { name: "disclaimer", path: "/disclaimer", ready: "main h1" },
  { name: "terms", path: "/terms", ready: "main h1" },
];

export const VIEWPORTS: [number, number][] = [
  [320, 568], [375, 812], [390, 844], [412, 915], [768, 1024], [1024, 768], [1280, 800], [1440, 900], [1920, 1080],
];
