import { useEffect, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

/* The legal documents. Versions must match backend/app/core/legal.py: when a document changes in a way members must
   re-accept, bump the version there and here. Drafted for review by qualified counsel before relying on them. */
const VERSION = "2026-10-04";
const EFFECTIVE = "4 October 2026";
const CONTACT = "nawaranarah@gmail.com";
const OPERATOR = "Nawar Anarah, an individual based in Dubai, United Arab Emirates, who operates Nexis Finance as an independent project";

type Doc = { path: string; title: string; summary: string; body: ReactNode };

function H({ id, children }: { id?: string; children: ReactNode }) {
  return <h2 id={id}>{children}</h2>;
}

const TERMS: Doc = {
  path: "/terms",
  title: "Terms of Use",
  summary: "The agreement between you and Nexis Finance when you use the website and its services.",
  body: (
    <>
      <p className="lg-callout">Nexis Finance provides financial information, analysis tools and discussion. It does not provide personalised investment advice, and nothing on Nexis is a recommendation to buy, sell or hold any investment. Investing involves risk, including the loss of the money you invest.</p>
      <H id="who">1. Who we are and what these Terms cover</H>
      <p>These Terms of Use ("Terms") govern your access to and use of the Nexis Finance website, applications and related services, including Nexis Pulse, My Nexis, the AI Advisor, market pages, reports and the Community feed (together, the "Service"). The Service is operated by {OPERATOR} ("Nexis", "we", "us").</p>
      <p>By creating an account, or by using the Service, you agree to these Terms. If you do not agree, do not use the Service. If you use the Service on behalf of an organisation, you confirm you have authority to bind it.</p>
      <H>2. Eligibility and accounts</H>
      <p>You must be at least 18 years old, or the age of majority where you live if higher, and legally able to enter into this agreement. You are responsible for your account, for keeping your sign-in details secure and for activity under your account. Tell us promptly at {CONTACT} if you believe your account has been compromised. We may refuse, suspend or close accounts as described in these Terms.</p>
      <H id="no-advice">3. Financial information, not advice</H>
      <ul>
        <li><b>No personalised advice.</b> The Service provides general information and educational tools. It does not take into account your objectives, financial situation or needs. Unless we expressly state otherwise in writing and are legally authorised to do so, nothing on the Service is investment, financial, legal, tax or accounting advice, or a solicitation or offer to buy or sell any security or financial product.</li>
        <li><b>Not a regulated service.</b> Nexis is not, by providing the Service, acting as a broker, dealer, investment adviser, portfolio manager or financial institution, and the Service is not a substitute for advice from a professional who is licensed where you live.</li>
        <li><b>Investment risk.</b> The value of investments can go down as well as up, and you may lose some or all of the money you invest. Past performance is not a reliable indicator of future results. Any scores, sentiment readings, scenarios or analyses are not predictions or guarantees.</li>
        <li><b>Your decisions are yours.</b> You are solely responsible for your investment decisions and for checking any information before relying on it. Consider seeking independent professional advice.</li>
      </ul>
      <H>4. Accuracy, third-party information and availability</H>
      <p>Market data, news, filings and other information on the Service come from third parties and automated systems. Prices may be delayed. Information may be incomplete, out of date or wrong, and AI-assisted content can contain errors. We do not guarantee the accuracy, completeness, timeliness or availability of any information, and we are not responsible for third-party content, websites or services that we link to. The Service is provided "as is" and "as available" and may be interrupted, changed or withdrawn, in whole or in part, at any time.</p>
      <H id="pulse">5. Nexis Pulse, anonymity and user content</H>
      <ul>
        <li><b>Anonymous public identity.</b> Posts, comments and replies you make in Nexis Pulse are shown publicly as "Anonymous". Your account remains linked to them internally so that we can operate and moderate the Service, prevent abuse, and comply with the law. Anonymity on Pulse is not anonymity from Nexis: we may disclose information about an account where required by law or a valid legal request (see section 10 and the Privacy Policy).</li>
        <li><b>Your content.</b> You are responsible for what you post. You confirm you have the right to post it and that it complies with these Terms and the <Link to="/community-guidelines">Community Guidelines</Link>. You keep ownership of your content. You grant Nexis a worldwide, non-exclusive, royalty-free, transferable and sublicensable licence to host, store, reproduce, display, adapt (for example for formatting or translation), distribute and analyse your content in connection with operating, improving and promoting the Service, including summarising discussions. This licence continues for content that others have relied on (for example replies to it) after you delete it, to the extent needed to keep threads intelligible, but we erase the text of content you delete.</li>
        <li><b>Opinions.</b> Content posted by members is their own opinion. Nexis does not endorse it and does not verify it.</li>
        <li><b>Public discussions from other sites.</b> Pulse also shows threads publicly posted on other sites (currently Reddit, Hacker News and StockTwits), labelled with where they come from. Nexis removes usernames and shows only short excerpts, each linked to the original. These are the views of people on those sites, not of Nexis or its members, and Nexis does not verify them. If you wrote something shown here, or hold rights in it, and want it removed, contact {CONTACT} or use Report and we will take it down.</li>
        <li><b>Nexis editorial content</b> is labelled "Nexis" and may be prepared with AI assistance as described in the <Link to="/ai-disclosure">AI Disclosure</Link>. It is context, not advice.</li>
      </ul>
      <H id="prohibited">6. Prohibited conduct</H>
      <p>You must not use the Service to:</p>
      <ul>
        <li>impersonate any person or organisation, including a company, its officers or employees, an analyst or a Nexis employee, or misrepresent your affiliation with anyone;</li>
        <li>manipulate or attempt to manipulate any market, including by coordinating buying or selling, "pump and dump" schemes, spreading information you know or suspect is false, or making misleading promotional claims;</li>
        <li>share, solicit or claim to have material non-public ("inside") information, or trade on it;</li>
        <li>promise or advertise guaranteed or risk-free returns, run or promote scams, or solicit money, investments, deposits or contact off the Service for any financial scheme;</li>
        <li>harass, threaten, abuse or discriminate against others, or post hateful, violent, sexual or otherwise illegal content;</li>
        <li>post other people's private or personal information;</li>
        <li>spam, post repetitive or automated content, or artificially inflate engagement, including by operating multiple accounts, using bots, or coordinating reactions, reports or replies;</li>
        <li>infringe intellectual property or other rights, including by copying paywalled articles;</li>
        <li>access the Service by automated means (scraping, crawling) except as allowed by our robots.txt or a written agreement, attempt to identify anonymous users, probe or bypass security or rate limits, or interfere with the Service.</li>
      </ul>
      <H id="moderation">7. Moderation and enforcement</H>
      <p>We may (but are not obliged to) review, flag, hide, restrict, remove or refuse any content, lock discussions, limit features, or suspend or terminate accounts, where we reasonably believe that content or conduct breaks these Terms or the law, creates risk for others or for Nexis, or where required by law. We use automated checks to flag content for human review and may hold flagged content from public view while it is reviewed. We aim to act proportionately, but we are not responsible for content posted by members, and our decision not to act in one case does not waive our rights in another.</p>
      <H>8. Intellectual property</H>
      <p>The Service, including its software, design, text, graphics, data compilations and Nexis editorial content, is owned by Nexis or its licensors and is protected by intellectual property laws. We grant you a limited, revocable, non-exclusive, non-transferable licence to use the Service for your personal, non-commercial purposes in line with these Terms. Third-party marks and content belong to their owners. If you believe content on the Service infringes your rights, contact {CONTACT}.</p>
      <H>9. Account security and your data</H>
      <p>Keep your password confidential and use a unique password. Information you add to My Nexis (investments, watchlists, notes) is private to your account and handled as described in the <Link to="/privacy">Privacy Policy</Link>. Nexis never asks for your brokerage or bank login.</p>
      <H id="legal-requests">10. Legal requests</H>
      <p>We may preserve and disclose account information and content, including information linking an account to anonymous posts, where we believe in good faith that this is required by applicable law, regulation, court order or other valid legal process, or is necessary to protect the rights, property or safety of Nexis, our users or the public, or to investigate fraud, market abuse or security issues.</p>
      <H>11. Limitation of liability</H>
      <p>To the fullest extent permitted by applicable law, Nexis and its affiliates, officers, employees and suppliers will not be liable for any indirect, incidental, special, consequential or punitive damages, or for any loss of profits, revenue, investments, data or goodwill, arising from or related to your use of, or inability to use, the Service or any content on it, including any investment decision you make. To the fullest extent permitted by law, our total liability for any claim relating to the Service is limited to the greater of the amount you paid us for the Service in the twelve months before the claim and USD 100. Nothing in these Terms excludes or limits liability that cannot be excluded or limited under applicable law, including liability for fraud or for death or personal injury caused by negligence, and nothing affects rights you have as a consumer that cannot be waived.</p>
      <H>12. Indemnity</H>
      <p>To the extent permitted by law, you agree to indemnify Nexis against claims, losses and reasonable costs arising from content you post or your breach of these Terms or the law.</p>
      <H>13. Termination</H>
      <p>You may stop using the Service and delete your account at any time in Account settings. We may suspend or terminate your access if you break these Terms, if required by law, or if we discontinue the Service. Sections that by their nature should survive termination (including 5, 8, 10, 11, 12 and 15) survive.</p>
      <H>14. Changes</H>
      <p>We may update these Terms. When changes are material we will tell you in the Service and ask you to accept the updated Terms before you post again. The version and effective date are shown at the top of this page.</p>
      <H>15. Governing law and disputes</H>
      <p>These Terms are governed by the laws of the Emirate of Dubai and the federal laws of the United Arab Emirates as applied in Dubai, without regard to conflict-of-law rules, and the courts of Dubai have jurisdiction, except where mandatory local law gives you the right to bring proceedings elsewhere.</p>
      <H>16. Contact</H>
      <p>Questions about these Terms: {CONTACT}.</p>
    </>
  ),
};

const PRIVACY: Doc = {
  path: "/privacy",
  title: "Privacy Policy",
  summary: "What personal information Nexis collects, why, who sees it, and your choices.",
  body: (
    <>
      <p className="lg-callout">Pulse is anonymous to other people: your name, email, phone, profile and investments are never shown next to what you post. Nexis itself keeps the link between your account and your posts, and only uses it to run, protect and moderate the Service and to meet legal obligations.</p>
      <H>1. Who is responsible</H>
      <p>{OPERATOR} is responsible for personal information processed through the Service. Contact: {CONTACT}.</p>
      <H>2. What we collect</H>
      <ul>
        <li><b>Account information:</b> email address and/or mobile number, a password hash (never the password itself), the sign-in method (for example Google), interface language, and the versions of the Terms and Privacy Policy you accepted and when.</li>
        <li><b>Content:</b> Pulse discussions, comments, reactions, follows, saves and reports; Community (Finstagram) posts and profile details if you use that feature, which is public by design.</li>
        <li><b>My Nexis:</b> investments (asset, quantity, purchase price and date, notes), your watchlist, notification preferences and the notifications we create for you.</li>
        <li><b>Public posts from other sites:</b> when Pulse shows a public thread from Reddit, Hacker News or StockTwits, Nexis stores the thread title, short excerpts of replies, their links and times. It removes usernames and mentions and does not keep anything about the people who wrote them. Ask at {CONTACT} to have an excerpt removed.</li>
        <li><b>Technical information:</b> session tokens (stored only as a cryptographic hash), request metadata needed for security and rate limiting (for example IP address, kept briefly), and error logs.</li>
      </ul>
      <p>We do not collect brokerage or bank credentials, and we do not buy personal data about you.</p>
      <H>3. How we use it</H>
      <ul>
        <li>to provide the Service — your account, My Nexis, notifications and personalised Pulse feeds (such as "For you");</li>
        <li>to keep Pulse safe — rate limiting, abuse prevention, automated safety checks, moderation, and enforcing the Terms;</li>
        <li>to comply with law and respond to valid legal requests;</li>
        <li>to maintain, secure and improve the Service.</li>
      </ul>
      <p>Depending on where you live, our legal bases are performance of our contract with you, our legitimate interests in operating a safe service, compliance with legal obligations, and your consent where we ask for it.</p>
      <H>4. Who can see what</H>
      <ul>
        <li><b>The public</b> sees your Pulse content as "Anonymous". Public responses from our servers do not contain your account identifier, name, email, phone, investments or moderation history.</li>
        <li><b>Moderators</b> see reported or flagged content and the reasons, but not who wrote or reported it. Actions affecting an account (for example pausing posting) are applied through the content without revealing identity to the moderator.</li>
        <li><b>Only you</b> see your investments, watchlist, notes, notifications, saved and followed discussions, and the list of your own Pulse posts.</li>
        <li><b>Service providers</b> that host, store or process data for us (for example cloud hosting, databases, and AI model providers for editorial and summarisation features) act under contract. We send AI providers public market information and public discussion text, not your account details or private investments.</li>
        <li><b>Authorities</b>, where required by law or valid legal process, as described in the Terms.</li>
      </ul>
      <p>We do not sell your personal information.</p>
      <H>5. Retention</H>
      <p>We keep account information while your account is open. If you delete your account, we delete your account, investments, watchlist and notifications, and we erase the text of your Pulse posts and detach them from any account; replies by others remain. We may keep limited records longer where required by law, to resolve disputes or to enforce our Terms (for example moderation records relating to illegal content).</p>
      <H>6. Your rights and choices</H>
      <p>Depending on where you live, you may have rights to access, correct, delete, restrict or object to processing of your personal information, and to data portability. You can change most information and delete your account in Account settings, and switch notifications off in Notifications. For other requests contact {CONTACT}. You may also complain to your data protection authority.</p>
      <H>7. Security</H>
      <p>We use measures such as salted password hashing, hashed session tokens, HTTP-only secure cookies, access controls that check ownership on the server for every private request, and cross-site request protection. No system is perfectly secure; tell us at {CONTACT} if you find a vulnerability.</p>
      <H>8. International transfers</H>
      <p>Our providers may process data in other countries. Where required, we use appropriate safeguards for such transfers.</p>
      <H>9. Children</H>
      <p>The Service is not intended for anyone under 18, and we do not knowingly collect their information.</p>
      <H>10. Changes</H>
      <p>We will post changes here with a new version and effective date, and ask you to acknowledge material changes before you post again.</p>
    </>
  ),
};

const DISCLAIMER: Doc = {
  path: "/disclaimer",
  title: "Financial Disclaimer",
  summary: "Nexis provides information and tools, not personalised investment advice.",
  body: (
    <>
      <p className="lg-callout">Nothing on Nexis Finance is a recommendation to buy, sell or hold any investment, or personalised financial advice.</p>
      <ul>
        <li><b>General information only.</b> Content on Nexis — market data, analysis, Nexis editorial discussions, AI Advisor answers, reports, scores and community posts — is general information. It does not consider your objectives, financial situation or needs.</li>
        <li><b>Not licensed advice.</b> Nexis does not hold itself out as a licensed investment adviser, broker or other regulated financial institution, and the Service is not a substitute for advice from a professional licensed where you live.</li>
        <li><b>Risk.</b> All investing involves risk. You can lose some or all of the money you invest. Leveraged products, crypto-assets and concentrated positions can be especially volatile. Past performance does not guarantee future results.</li>
        <li><b>No guarantees.</b> We do not guarantee the accuracy, completeness or timeliness of any information, or any outcome or return. Prices may be delayed. Sources can be wrong. AI-assisted content can contain errors.</li>
        <li><b>Community content</b> on Pulse is the opinion of anonymous members. Nexis does not verify or endorse it. Be especially sceptical of anyone promising returns, claiming inside information or asking you to act quickly or contact them elsewhere.</li>
        <li><b>Do your own research</b> and consider independent professional advice before making financial decisions.</li>
      </ul>
    </>
  ),
};

const GUIDELINES: Doc = {
  path: "/community-guidelines",
  title: "Community Guidelines",
  summary: "How to take part in Nexis Pulse: argue hard about ideas, never against people or the market's integrity.",
  body: (
    <>
      <p className="lg-callout">Pulse exists for honest financial debate. Disagreement is welcome. Negative opinions are welcome. Bullish and bearish arguments are both welcome. Challenge the reasoning — not the person.</p>
      <H>What good looks like</H>
      <ul>
        <li>Explain why you think something, not just what you think.</li>
        <li>Say what would change your mind. Separate facts (with a source) from opinion.</li>
        <li>Disagree directly and specifically. "I disagree — the margin guidance implies…" is better than "you're wrong".</li>
        <li>Remember everyone is anonymous here, including you. Anonymity is for honest debate, not for avoiding responsibility.</li>
      </ul>
      <H>Not allowed on Pulse</H>
      <ul>
        <li><b>Impersonation</b> — pretending to be a real person, a company, a company insider, an analyst, a regulator or Nexis.</li>
        <li><b>Fabricated inside information</b> — claiming or sharing material non-public information, real or invented.</li>
        <li><b>Market manipulation</b> — coordinating buying or selling ("everyone buy at the open"), pump-and-dump, or deliberately spreading false information to move a price.</li>
        <li><b>Scams and solicitation</b> — guaranteed or risk-free returns, paid "signal" groups, requests to move to WhatsApp or Telegram, wallet addresses, referral links, or asking anyone to send money.</li>
        <li><b>Knowing misinformation</b> — stating something you know is false as fact. (Being wrong, or holding an unpopular view, is not misinformation.)</li>
        <li><b>Harassment</b> — threats, insults, slurs, hate, or targeting someone.</li>
        <li><b>Illegal content</b> and anything that breaks the law where you are.</li>
        <li><b>Spam</b> — repetitive, off-topic or automated posting, and promotional content.</li>
        <li><b>Private information</b> — anyone's personal details, or attempts to unmask anonymous users.</li>
        <li><b>Fake engagement</b> — multiple accounts, bots, or coordinating reactions, reports or replies.</li>
      </ul>
      <H>How moderation works</H>
      <p>Automated checks look for the patterns above (for example promised returns or requests to move off Nexis). Most flagged posts stay visible and are queued for review; a few high-risk patterns are held until a moderator looks. Members can report posts, and moderators decide: approve, remove, lock a discussion, or pause an account's posting. Moderators see the content and the report reasons — never who posted or who reported. We don't remove posts for being controversial, bearish, or wrong about the market.</p>
      <p>Breaking these guidelines can lead to removal of content, a pause on posting, or closure of the account, as set out in the <Link to="/terms">Terms of Use</Link>.</p>
    </>
  ),
};

const AI: Doc = {
  path: "/ai-disclosure",
  title: "AI Disclosure",
  summary: "Where Nexis uses AI, and where it never does.",
  body: (
    <>
      <p className="lg-callout">AI helps Nexis organise information. It never pretends to be a person. Every post labelled "Anonymous" was written by a real member.</p>
      <H>Where AI is used</H>
      <ul>
        <li><b>Nexis editorial discussions</b> may be drafted with AI from a numbered list of sourced news, filings and market data: what happened, the bull and bear cases, open questions. Each point cites its sources, figures are checked against the sources automatically, and pieces are marked "AI-assisted". When no AI model is available, Nexis assembles the same sections from the sources with fixed rules and says so.</li>
        <li><b>Summaries and briefs</b> — for example the AI brief in My Nexis — are generated on request from the sources shown.</li>
        <li><b>The AI Advisor</b> answers your questions using live data tools; its answers are labelled as AI.</li>
        <li><b>Classification</b> — topics and tone of news headlines, used to organise editorial context, and sorting replies in public threads from other sites into "agrees", "pushes back", "asks" or "adds context". The replies themselves are quoted, never rewritten.</li>
      </ul>
      <H>Where AI is never used</H>
      <ul>
        <li>AI does not write community posts, comments or replies, and does not create accounts. Quotes from other sites are real excerpts, labelled with the site they came from — never presented as Nexis members.</li>
        <li>AI does not create reactions, follows, saves or any other engagement, and Pulse never shows invented activity. If nobody has replied, it says so.</li>
        <li>AI output is never presented as the opinion or experience of an investor.</li>
      </ul>
      <H>Limitations</H>
      <p>AI can make mistakes, miss context or overweight a source. Treat AI-assisted content as a starting point, check the linked sources, and read the <Link to="/disclaimer">Financial Disclaimer</Link>.</p>
    </>
  ),
};

export const LEGAL_DOCS: Doc[] = [TERMS, PRIVACY, DISCLAIMER, GUIDELINES, AI];

export default function Legal() {
  const { pathname } = useLocation();
  const doc = LEGAL_DOCS.find((d) => d.path === pathname) ?? TERMS;
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);
  return (
    <div className="lg">
      <nav className="lg-nav" aria-label="Legal documents">
        {LEGAL_DOCS.map((d) => <Link key={d.path} to={d.path} className={d.path === doc.path ? "on" : ""} aria-current={d.path === doc.path ? "page" : undefined}>{d.title}</Link>)}
      </nav>
      <article className="lg-doc">
        <div className="np-eyebrow"><span>Nexis Finance</span><span>Version {VERSION}</span><span>Effective {EFFECTIVE}</span></div>
        <h1>{doc.title}</h1>
        <p className="lg-summary">{doc.summary}</p>
        {doc.body}
      </article>
    </div>
  );
}
