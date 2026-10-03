"""Nexis Research editorial discussions.

Each entry is published by the official Nexis Research account (see ``app/seeds/research.py``).
``key`` is permanent: it identifies the discussion in the database, so edits to the text are applied in place.
Never reuse or rename a key. ``days_ago`` places the discussion in the past relative to the first seeding run.
Topics must be keys from ``app.services.pulse.TOPICS``; sentiment is bullish, neutral or bearish.
"""

from __future__ import annotations

from typing import Any

DISCUSSIONS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ NVIDIA
    {
        "key": "nvda-001", "symbol": "NVDA", "name": "NVIDIA Corporation", "sentiment": "bullish", "topics": ["ai", "growth"], "days_ago": 78,
        "title": "Nvidia's next growth phase may depend more on inference than on training",
        "body": """Most of the debate around Nvidia still centres on how many training clusters the largest labs will build. That framing made sense during the first wave of generative AI, when demand came in large, lumpy orders from a handful of buyers.

The argument for a second leg is that inference behaves differently. Once a model is deployed inside a product, it runs every time someone uses it, so compute demand starts to scale with usage rather than with research budgets. If AI features keep spreading into search, office software, coding tools and customer service, inference could become the steadier and larger part of the market.

The counterpoint is that inference is more price-sensitive than training. Buyers care about cost per query, which invites optimisation, smaller models and custom chips designed by the cloud providers themselves. Nvidia's answer has been to sell the full stack, including networking and software, rather than just the accelerator.

The variables we would watch are management commentary on the inference share of data-centre demand, how pricing holds as product generations change, and whether software keeps customers inside the CUDA ecosystem as alternatives improve.""",
    },
    {
        "key": "nvda-002", "symbol": "NVDA", "name": "NVIDIA Corporation", "sentiment": "neutral", "topics": ["valuation", "earnings"], "days_ago": 41,
        "title": "Nvidia's quarterly beats now matter less than the size of next year's capex budgets",
        "body": """For several quarters the market's reaction to Nvidia results has been driven less by the reported numbers than by what they imply about the year ahead. That is a sign of how much good news is already expected.

The useful way to think about the stock today is as a derivative of the capital spending plans of its largest customers. When the big cloud providers raise their capex guidance, Nvidia's forward estimates tend to move with them; when they talk about efficiency or digesting capacity, sentiment cools quickly even if Nvidia itself has not changed its outlook.

That makes the valuation debate less about this year's earnings multiple and more about durability. A multiple that looks reasonable on next year's profits can look stretched if spending plateaus the year after.

We do not think the evidence yet points either way decisively. Demand commentary remains strong, but the concentration of that demand means the stock can be volatile around customers' own earnings calls. Investors who own it should probably pay as much attention to hyperscaler capex guidance as to Nvidia's own reports.""",
    },
    {
        "key": "nvda-003", "symbol": "NVDA", "name": "NVIDIA Corporation", "sentiment": "bearish", "topics": ["risk", "competition"], "days_ago": 19,
        "title": "Customer concentration is the part of the Nvidia story that gets too little attention",
        "body": """A large share of Nvidia's data-centre revenue comes from a small group of very large buyers. Those same companies have the resources and the motivation to reduce their dependence on a single supplier, and several of them already design their own AI accelerators.

None of that means Nvidia loses its position quickly. Custom chips take years to mature, and they tend to be built for a narrower range of workloads. But concentration changes the negotiating balance. The more of the market sits with a few buyers, the more those buyers can push on price, split orders between suppliers, or shift internal workloads onto their own silicon.

The risk is not a sudden collapse in demand. It is a gradual erosion of pricing power that shows up first in gross margins rather than in revenue.

What would ease this concern is evidence that demand is broadening: more enterprises, sovereign AI projects and smaller cloud providers buying directly. Until that becomes clearer in the numbers, we think the concentration risk deserves more weight than the market currently gives it.""",
    },
    {
        "key": "nvda-004", "symbol": "NVDA", "name": "NVIDIA Corporation", "sentiment": "bullish", "topics": ["margins", "products"], "days_ago": 4,
        "title": "Holding gross margins through product transitions would show Nvidia's pricing power is intact",
        "body": """Product transitions are where hardware companies usually give up margin. New generations need new manufacturing processes, early yields are lower, and customers wait for the latest part rather than buy the old one at full price.

Nvidia has so far managed these transitions better than most. If it can keep gross margins broadly stable while moving customers to its next platform, that would be the clearest sign that its competitive position is still holding up, more convincing than any revenue figure.

The bullish reading is that Nvidia sells systems rather than chips. Networking, interconnect and software are bundled into a platform that is hard to replicate piece by piece, and that supports pricing even as competitors improve individual accelerators.

The risk to this view is a period of supply catching up with demand, which historically is when semiconductor margins compress. We would treat any guidance for a sustained margin decline, rather than a temporary dip around a launch, as the signal that the pricing argument is weakening.""",
    },
    # ------------------------------------------------------------------ Apple
    {
        "key": "aapl-001", "symbol": "AAPL", "name": "Apple Inc.", "sentiment": "bullish", "topics": ["growth", "revenue"], "days_ago": 70,
        "title": "Apple's services growth changes how much the hardware cycle should matter",
        "body": """For most of its history Apple was valued as a hardware company, and its share price rose and fell with the iPhone upgrade cycle. That lens is less useful than it used to be.

Services such as the App Store, iCloud, payments, subscriptions and licensing now make up a meaningful share of revenue and an even larger share of gross profit, because their margins are much higher than device margins. Every iPhone sold adds to an installed base that keeps generating services revenue for years.

This is why a weak upgrade year no longer has the same impact on earnings. The installed base keeps growing slowly even when unit sales are flat, and monetisation per user tends to rise.

The counterargument is that a significant part of services income depends on arrangements that regulators are examining, including App Store commissions and the default search payment from Google. We see that as the main risk to the thesis. But as long as the installed base keeps expanding, we think the market is right to treat Apple more as a platform than as a phone maker.""",
    },
    {
        "key": "aapl-002", "symbol": "AAPL", "name": "Apple Inc.", "sentiment": "neutral", "topics": ["ai", "products"], "days_ago": 33,
        "title": "Apple doesn't need to win the AI model race, but it does need a reason to upgrade",
        "body": """Apple is often described as behind in AI because it does not build the largest frontier models. We think that misses how Apple usually competes. It rarely invents a category first; it waits until it can integrate a technology into devices people already use.

The real question is whether AI features become a reason for people to replace phones sooner. Apple's installed base is enormous, and many users hold devices for several years. Features that run on newer chips could shorten that cycle, which would matter far more to revenue than any model benchmark.

So far the evidence is mixed. Early AI features have been useful but not compelling enough to drive a visible upgrade wave, and partnerships with outside model providers suggest Apple is willing to buy rather than build some capabilities.

We are neutral here. Apple's strengths in privacy, on-device processing and distribution are real advantages, but until upgrade data shows AI is changing buying behaviour, it is hard to argue that AI is a new growth driver rather than a feature that keeps Apple competitive.""",
    },
    {
        "key": "aapl-003", "symbol": "AAPL", "name": "Apple Inc.", "sentiment": "bearish", "topics": ["regulation", "risk"], "days_ago": 9,
        "title": "App Store rules are the regulatory risk most likely to reach Apple's margins",
        "body": """Regulatory pressure on Apple has been building on several fronts, but the one with the most direct financial impact is the App Store. Commissions on digital purchases are a high-margin revenue stream, and they are exactly what regulators in Europe, the US and elsewhere are focused on.

Rules that allow alternative payment systems, outside app stores or links to cheaper offers do not have to eliminate Apple's commission to hurt it. They only need to give large developers enough leverage to negotiate lower effective rates or move some transactions elsewhere.

Apple has so far responded with complex compliance changes that keep much of its economics intact, which suggests the near-term damage may be limited. But each round of regulation tends to be followed by another, and the direction of travel is clear.

We are cautious because services margins carry a large share of Apple's profit growth. If commission rates drift lower over the next few years, the services segment could grow revenue while contributing less to earnings than investors currently assume.""",
    },
    # ------------------------------------------------------------------ Microsoft
    {
        "key": "msft-001", "symbol": "MSFT", "name": "Microsoft Corporation", "sentiment": "bullish", "topics": ["ai", "growth"], "days_ago": 64,
        "title": "Azure growth is the clearest number for judging whether Microsoft's AI spending is paying off",
        "body": """Microsoft has committed very large sums to AI infrastructure, and investors are right to ask what they are getting in return. The most direct answer is in Azure.

Azure growth captures demand for AI services, including access to models through Microsoft's cloud, as well as the traditional migration of corporate workloads. Management has been breaking out how much AI contributes to growth, which makes it easier to judge whether the new capacity is being used.

The bullish case is that Microsoft monetises AI in several layers at once: infrastructure through Azure, applications through Copilot in Office and GitHub, and its existing enterprise relationships, which make it easier to sell new products to customers who already trust it.

The risk is that capacity constraints, rather than demand, have been limiting Azure growth, and that once supply catches up growth could normalise. We would watch whether Azure growth holds up as new data centres come online. If it does, that is strong evidence the spending is backed by real demand rather than speculative building.""",
    },
    {
        "key": "msft-002", "symbol": "MSFT", "name": "Microsoft Corporation", "sentiment": "neutral", "topics": ["margins", "ai"], "days_ago": 27,
        "title": "Microsoft's capital spending is growing faster than the revenue it is meant to support",
        "body": """Microsoft's investment in data centres and chips has risen sharply, and for now capital spending is growing faster than revenue. That is normal at the start of a build-out, but it changes the financial profile of a business that investors have long valued for its very high returns on capital.

Higher capex flows into depreciation over the following years. Even if revenue keeps growing, operating margins can come under pressure as those depreciation charges rise, especially if AI services are priced competitively to win share.

Microsoft's defenders point out that the company has absorbed large investments before and that its software margins give it room. They also note that much of the infrastructure is long-lived and will serve demand for many years.

We sit in the middle. The spending looks rational given the demand signals, but the payback period is uncertain, and free cash flow growth may lag earnings for a while. Investors who own Microsoft for its cash generation should expect a few years in which that cash goes back into the business rather than to shareholders at the same pace as before.""",
    },
    {
        "key": "msft-003", "symbol": "MSFT", "name": "Microsoft Corporation", "sentiment": "bullish", "topics": ["products", "revenue"], "days_ago": 6,
        "title": "Copilot seat pricing tests whether companies will pay extra for AI inside software they already use",
        "body": """The commercial question behind Microsoft's AI strategy is simple: will companies pay an additional monthly fee per employee for AI features in Office, Teams and other tools they already pay for?

If the answer is yes at scale, the economics are attractive. Microsoft already sells to most large employers, so it does not need to win new customers; it needs to raise revenue per seat. Even modest adoption across a very large installed base would add meaningfully to growth.

Early adoption has been gradual. Companies tend to start with pilots, measure productivity, and expand only where the benefit is clear. That slows the ramp but also makes the revenue that does arrive more durable, because it is based on demonstrated use rather than hype.

The bearish counterargument is that price-sensitive customers may treat AI as something that should come bundled, forcing Microsoft to include it at lower prices. We lean positive because Microsoft's distribution advantage is hard to match, but the adoption curve, not announcements, is what to watch.""",
    },
    # ------------------------------------------------------------------ Tesla
    {
        "key": "tsla-001", "symbol": "TSLA", "name": "Tesla, Inc.", "sentiment": "bullish", "topics": ["products", "growth"], "days_ago": 84,
        "title": "Tesla's autonomy business is the part of the company most models struggle to value",
        "body": """Traditional valuation methods treat Tesla as a car company with a premium multiple. That approach leaves out the part of the business shareholders are arguably paying the most for: driverless vehicles and the software behind them.

The argument for autonomy is about fleet economics. A vehicle that can operate as a robotaxi earns revenue for many hours a day instead of sitting in a garage, and the software can be updated across millions of cars already on the road. If that works at scale, the profit potential looks very different from selling cars at a one-off margin.

The difficulty is that timelines in autonomy have repeatedly slipped across the whole industry, and regulators approve services city by city. Early robotaxi operations are a meaningful step, but they are a long way from a nationwide network.

We think the bullish case is coherent rather than certain. The useful signals are the number of cities with approved driverless operations, the safety data that supports expansion, and whether the service can run without safety monitors. Those matter more than demonstrations.""",
    },
    {
        "key": "tsla-002", "symbol": "TSLA", "name": "Tesla, Inc.", "sentiment": "bearish", "topics": ["margins", "competition"], "days_ago": 52,
        "title": "Tesla's margin story is becoming as important as its delivery numbers",
        "body": """For years the market focused on how many cars Tesla delivered each quarter. Today the more important figure may be what it earns on each one.

Price reductions have helped Tesla defend volumes as competition has intensified, especially in China, where domestic manufacturers offer well-equipped electric vehicles at lower prices and launch new models quickly. The cost of that defence has been lower automotive gross margins.

Tesla still has real cost advantages in manufacturing, and its scale lets it absorb pressure that smaller rivals cannot. But when margins fall alongside slowing growth, the core car business supports a smaller share of the valuation, which puts more weight on future projects such as autonomy and robotics.

The risk we see is a period in which the auto business neither grows quickly nor earns high margins while investors wait for newer businesses to mature. A credible lower-cost model that restores volume growth without further price cuts would weaken this concern, but until then we are cautious about the near-term earnings picture.""",
    },
    {
        "key": "tsla-003", "symbol": "TSLA", "name": "Tesla, Inc.", "sentiment": "bullish", "topics": ["energy", "growth"], "days_ago": 23,
        "title": "Energy storage is becoming a Tesla business that investors can't ignore",
        "body": """Tesla's energy segment rarely features in debates about the stock, yet it has grown into a substantial business. Large battery systems for utilities and grid operators have seen strong demand as more renewable generation needs storage to smooth supply.

What makes this interesting is not only the growth rate but the margin profile. Storage has been more profitable than parts of the car business recently, and demand is tied to utility investment cycles rather than consumer spending.

There are fair caveats. The segment is still much smaller than automotive, it competes with large battery suppliers that have strong cost positions, and project revenue can be uneven from quarter to quarter. Policy support for grid storage also differs across markets.

Still, we think energy deserves more attention than it gets. A business that grows steadily, earns solid margins and has little exposure to the price war in cars gives Tesla a degree of diversification the market rarely credits.""",
    },
    {
        "key": "tsla-004", "symbol": "TSLA", "name": "Tesla, Inc.", "sentiment": "bearish", "topics": ["valuation", "risk"], "days_ago": 2,
        "title": "Tesla's valuation leaves little room for delays in autonomy",
        "body": """Whatever one thinks of Tesla's long-term ambitions, the current valuation already assumes a lot of them succeed. On near-term earnings from the existing businesses, the multiple is far above other carmakers and well above most large technology companies.

That means the share price is unusually sensitive to news about autonomy, robotics and regulation. Positive developments can move the stock sharply, but so can delays, safety incidents or slower regulatory approvals, because they push back the profits that justify the price.

The bull case is not unreasonable: if driverless services scale, today's price could look cheap in hindsight. But investors should be clear about which outcome they are paying for.

Our view is that the risk-reward is skewed toward disappointment over the next year or two. Autonomy timelines have slipped before, and the core auto business is not currently growing fast enough to carry the valuation on its own.""",
    },
    # ------------------------------------------------------------------ Amazon
    {
        "key": "amzn-001", "symbol": "AMZN", "name": "Amazon.com, Inc.", "sentiment": "bullish", "topics": ["ai", "growth"], "days_ago": 73,
        "title": "AWS reaccelerating would matter more to Amazon's value than faster retail growth",
        "body": """Amazon's retail business is much larger by revenue, but AWS produces a large share of the company's operating profit. That makes the direction of cloud growth the single most important variable for the stock.

AI workloads give AWS a new source of demand on top of the ongoing move of corporate IT into the cloud. Amazon is pursuing this on several fronts: hosting third-party models, building its own chips for training and inference, and offering tools that let companies build applications on top of their own data.

The bullish argument is that AWS's large existing customer base makes it a natural home for enterprise AI, much as it became the default for cloud computing. Custom chips could also help margins by reducing reliance on expensive third-party accelerators.

The risk is that competitors have moved faster in some AI services, and customers may spread workloads across providers. We would watch AWS growth relative to the other large clouds and the backlog of contracted revenue. Sustained reacceleration would support a higher value for the whole company.""",
    },
    {
        "key": "amzn-002", "symbol": "AMZN", "name": "Amazon.com, Inc.", "sentiment": "neutral", "topics": ["margins", "earnings"], "days_ago": 38,
        "title": "Amazon's retail margins have improved; the question is how much of that is permanent",
        "body": """Amazon's North American and international retail segments have become noticeably more profitable after years of thin or negative margins. A regionalised delivery network, tighter cost control and growth in higher-margin revenue streams have all contributed.

Some of that improvement looks structural. Shorter delivery distances lower the cost per package, and automation in warehouses reduces labour intensity over time.

Other parts are harder to judge. Amazon has a long history of reinvesting profits when it sees an opportunity, whether in faster delivery, new categories or international expansion. Periods of strong margins have often been followed by heavier spending.

We are neutral on how much of the margin gain will stick. The business is clearly more efficient than it was, but Amazon's management has never treated retail margins as a target to maximise. Investors should expect margins to move with Amazon's appetite for investment rather than rise in a straight line.""",
    },
    {
        "key": "amzn-003", "symbol": "AMZN", "name": "Amazon.com, Inc.", "sentiment": "bullish", "topics": ["revenue", "margins"], "days_ago": 12,
        "title": "Advertising has quietly become one of Amazon's most profitable businesses",
        "body": """Amazon's advertising revenue gets less attention than AWS, but it has grown into one of the largest digital ad businesses in the world. Sponsored product listings on Amazon's own store make up most of it.

The economics are unusually good. Advertisers are reaching shoppers at the moment they are about to buy, and Amazon can show them exactly how many sales the ads produced. That closed loop between ad spend and purchases is something most other platforms cannot offer.

Because the ads sit inside Amazon's existing store, the extra cost of serving them is small, so a large share of ad revenue flows through to profit. That helps explain why the retail segments have looked more profitable recently.

The main risk is ad load. Too many sponsored results can make the shopping experience worse and erode trust in search rankings. There is also growing competition from retailers building their own ad networks. Even so, we see advertising as a durable profit engine.""",
    },
    # ------------------------------------------------------------------ Alphabet
    {
        "key": "googl-001", "symbol": "GOOGL", "name": "Alphabet Inc.", "sentiment": "neutral", "topics": ["ai", "competition"], "days_ago": 81,
        "title": "AI answers are the first real test of Google Search economics in years",
        "body": """Search advertising has been one of the most profitable businesses ever built, and for two decades its core mechanics barely changed: a query, a page of links, and ads alongside them. AI-generated answers change that format.

The worry is straightforward. If users get their answer directly, they may click fewer links and fewer ads, and AI answers cost more to produce than a traditional results page. Competitors offering chat-based search add pressure.

Google's response has been to put AI summaries into search itself rather than wait to be disrupted. So far it has reported that search usage and revenue continue to grow, which suggests the transition is not destroying the business.

We think it is too early to call. The key questions are whether ads inside AI answers monetise as well as traditional ads, and whether the higher cost of generating answers falls quickly enough. Google has the scale and in-house chips to manage costs better than most, but the margin profile of search could look different in a few years.""",
    },
    {
        "key": "googl-002", "symbol": "GOOGL", "name": "Alphabet Inc.", "sentiment": "bullish", "topics": ["growth", "ai"], "days_ago": 45,
        "title": "Google Cloud and in-house chips give Alphabet a second way to make money from AI",
        "body": """The debate about Alphabet and AI tends to focus on whether chatbots threaten search. That leaves out the part of the company that benefits most directly from AI demand: Google Cloud.

Cloud has moved from losses to solid profitability, and AI services are adding to growth. Alphabet's advantage is that it designs its own AI chips, which it uses internally and also offers to cloud customers. That gives it more control over costs than companies that rely entirely on outside suppliers.

There is also a strategic benefit. The same infrastructure that serves cloud customers powers Alphabet's own models and search, so investment is shared across several businesses rather than justified by one.

The risks are that Google Cloud remains smaller than the two largest providers and has to compete hard on price, and that capital spending is rising quickly. But we think the market still values Alphabet mainly as a search company, and that cloud and chips are underweighted in that view.""",
    },
    {
        "key": "googl-003", "symbol": "GOOGL", "name": "Alphabet Inc.", "sentiment": "bearish", "topics": ["regulation", "risk"], "days_ago": 15,
        "title": "Antitrust remedies are a slow but real risk to Alphabet's distribution deals",
        "body": """US courts have found that Google held an illegal monopoly in search, and separate cases target its advertising technology business. Remedies take years and appeals take longer, which makes it tempting to dismiss the issue. We think that would be a mistake.

A large part of Google's search dominance rests on distribution: being the default on browsers and phones through payments to companies such as Apple and Samsung. Restrictions on exclusive arrangements could give rival search and AI products a better chance to reach users, especially at a moment when AI is already changing how people look for information.

The ad technology case matters for a different reason. Structural changes there could reduce Google's take from advertising transactions across the wider web.

None of this threatens Alphabet's existence, and remedies so far have been less severe than some feared. But the combination of legal pressure and technological change makes the long-term durability of search margins less certain than the current valuation implies.""",
    },
    # ------------------------------------------------------------------ Meta
    {
        "key": "meta-001", "symbol": "META", "name": "Meta Platforms, Inc.", "sentiment": "bullish", "topics": ["revenue", "ai"], "days_ago": 67,
        "title": "AI-driven ad targeting is doing more for Meta's revenue than any new product launch",
        "body": """Meta's most visible AI work is its open models and chat assistants, but the most commercially important use of AI is less visible: the systems that decide which ads and posts each user sees.

After changes to mobile privacy rules made tracking harder, Meta rebuilt much of its advertising system around machine learning. Better models allow it to predict which ads will convert using less personal data, and automated campaign tools let advertisers hand more of the targeting and creative decisions to Meta.

The results have shown up in growth in ad impressions and pricing, and in a broader base of advertisers, including many smaller businesses for which Meta is the main online marketing channel.

The risk is that Meta's heavy spending on AI infrastructure and future products grows faster than these gains. But the core argument is that AI is not a speculative bet for Meta; it is already improving the profitability of its main business, and every improvement compounds across billions of users.""",
    },
    {
        "key": "meta-002", "symbol": "META", "name": "Meta Platforms, Inc.", "sentiment": "bearish", "topics": ["margins", "risk"], "days_ago": 30,
        "title": "Meta's spending plans ask investors to accept a long and uncertain payback period",
        "body": """Meta has guided to a significant increase in capital spending as it builds data centres for AI, and its Reality Labs division continues to lose large sums on virtual and augmented reality. Together they absorb a growing share of the cash produced by the advertising business.

The company's position is that these investments will define the next computing platform and keep its products competitive. That may prove right. But the returns are distant and hard to measure, and Meta has been here before: investors pushed back sharply when metaverse spending surged a few years ago.

What makes us cautious is the asymmetry. The advertising business is strong and profitable today, but it is exposed to economic cycles and competition for attention. If ad growth slows while spending keeps rising, margins could compress quickly.

We would want to see clearer evidence of revenue tied to the new spending, from AI products, business messaging or devices, before giving Meta full credit for these plans.""",
    },
    {
        "key": "meta-003", "symbol": "META", "name": "Meta Platforms, Inc.", "sentiment": "neutral", "topics": ["products", "valuation"], "days_ago": 5,
        "title": "For Meta, Reality Labs is now a question of patience rather than survival",
        "body": """When Meta first ramped up its metaverse spending, the concern was existential: was the company diverting its profits into a project with no clear market? That debate has calmed, mainly because the advertising business recovered strongly and the company became more disciplined about costs elsewhere.

Reality Labs still loses a lot of money each year. The difference now is that the losses are better understood and more clearly ring-fenced. Smart glasses have found a modest but real market, and headsets continue to improve, even if mass adoption remains distant.

For valuation, the practical approach is to treat Reality Labs as an option the company is funding from a very profitable core. On that basis Meta does not look expensive relative to other large platforms, but the losses do reduce how much of that core profit reaches shareholders.

We are neutral. The division could become valuable if wearable computing takes off, but we would not buy the stock for that reason alone.""",
    },
    # ------------------------------------------------------------------ AMD
    {
        "key": "amd-001", "symbol": "AMD", "name": "Advanced Micro Devices, Inc.", "sentiment": "bullish", "topics": ["ai", "competition"], "days_ago": 59,
        "title": "AMD needs only a modest share of AI accelerators for its numbers to change",
        "body": """The AI accelerator market is dominated by one supplier, which is exactly why AMD's opportunity is interesting. Large customers want a credible second source, both to improve their negotiating position and to reduce supply risk.

AMD does not need to match the leader to benefit. Even a modest share of a market of this size would represent a large increase in revenue relative to AMD's current data-centre business. Its accelerators are competitive on memory capacity, which matters for running large models, and it already has relationships with the big cloud providers through its server CPUs.

The bullish case also rests on AMD's broader data-centre position. Its server processors have taken share steadily over several years, showing the company can win against an entrenched competitor.

The main risks are execution and software, which we discuss separately, and the possibility that custom chips from the cloud providers take the 'second source' role instead. But the setup, in which customers actively want an alternative, is favourable for AMD.""",
    },
    {
        "key": "amd-002", "symbol": "AMD", "name": "Advanced Micro Devices, Inc.", "sentiment": "neutral", "topics": ["products", "earnings"], "days_ago": 26,
        "title": "Software, not hardware, is the constraint on how fast AMD can grow in AI",
        "body": """AMD's AI accelerators look competitive on paper, and in some workloads they perform very well. The harder problem is software. Most AI developers have spent years building on Nvidia's CUDA platform, and moving to a different stack takes time and engineering effort.

AMD has invested heavily in its open software platform and worked closely with the largest customers to optimise popular models. Those customers can afford dedicated engineering teams, which is why AMD's early wins have come from a few very large buyers rather than from the broad market.

This shapes how we read AMD's results. Revenue from AI can grow quickly from a few big deployments, but broad adoption depends on developers finding it just as easy to build on AMD hardware.

We are neutral because the progress is real but uneven. The signal to watch is whether AMD starts winning customers who do not have the resources to do the porting work themselves. That would show the software gap is closing.""",
    },
    {
        "key": "amd-003", "symbol": "AMD", "name": "Advanced Micro Devices, Inc.", "sentiment": "bearish", "topics": ["competition", "valuation"], "days_ago": 8,
        "title": "AMD's valuation leans heavily on AI while its other businesses face tougher competition",
        "body": """Much of AMD's recent valuation reflects expectations for its data-centre AI business. That leaves the rest of the company, PCs, gaming and embedded chips, carrying less weight in the investment case than its size would suggest.

Those businesses face their own pressures. PC demand is cyclical, gaming consoles follow long product cycles, and Intel is fighting to regain share in both client and server processors. Arm-based chips are also gaining ground in laptops and data centres.

The concern is not that AMD's AI ambitions are unrealistic, but that the stock prices in a lot of success there while the core businesses provide less of a cushion than they once did. If AI revenue ramps more slowly than expected, there is not much else to support the multiple.

We would be more comfortable if AI growth were accompanied by steady share gains in server CPUs and a recovery in PCs. Without those, the risk-reward looks less attractive than the AI narrative suggests.""",
    },
    # ------------------------------------------------------------------ Broadcom
    {
        "key": "avgo-001", "symbol": "AVGO", "name": "Broadcom Inc.", "sentiment": "bullish", "topics": ["ai", "growth"], "days_ago": 48,
        "title": "Custom AI chips give Broadcom a different kind of exposure to the AI build-out",
        "body": """Broadcom designs custom AI accelerators for some of the largest technology companies, which use them alongside or instead of off-the-shelf chips. It also supplies much of the networking hardware that connects AI clusters.

This gives Broadcom a different risk profile from a general-purpose chip vendor. Its customers commit to multi-year design programmes, which makes demand more visible, and as clusters grow larger the networking that links thousands of chips becomes as important as the chips themselves.

If the largest AI spenders keep shifting part of their workloads to custom silicon to control costs, Broadcom is one of the main beneficiaries, whichever model or application wins.

The risks are concentration in a few very large customers and the possibility that some of them bring more chip design in-house over time. Valuation has also risen with the AI theme. But we think the combination of custom chips and networking gives Broadcom durable exposure that does not depend on any single supplier keeping its lead.""",
    },
    {
        "key": "avgo-002", "symbol": "AVGO", "name": "Broadcom Inc.", "sentiment": "neutral", "topics": ["deals", "debt"], "days_ago": 11,
        "title": "VMware is Broadcom's quieter test, and it is about pricing power and debt",
        "body": """Broadcom's AI business gets most of the attention, but its acquisition of VMware was one of the largest technology deals in recent years and changed the company's financial profile.

Broadcom moved VMware customers from perpetual licences to subscription bundles, which raised revenue per customer and improved profitability quickly. It is a familiar Broadcom playbook: buy an established software franchise, focus on the largest customers and raise prices.

The question is how sustainable that is. Some customers have complained publicly about price increases and are evaluating alternatives. Migration away from VMware is difficult and slow, which gives Broadcom leverage, but it also gives customers years to plan an exit if they are unhappy.

The deal also added significant debt, which Broadcom has been paying down from strong cash flow. We are neutral: the integration has gone well financially, but the durability of VMware's pricing is the variable to watch over the next renewal cycles.""",
    },
    # ------------------------------------------------------------------ JPMorgan
    {
        "key": "jpm-001", "symbol": "JPM", "name": "JPMorgan Chase & Co.", "sentiment": "neutral", "topics": ["rates", "earnings"], "days_ago": 61,
        "title": "Lower interest rates would test how much of JPMorgan's earnings power is structural",
        "body": """Higher interest rates lifted net interest income across the banking industry, and JPMorgan benefited more than most because of its large base of low-cost deposits. The question now is what happens to earnings as rates come down.

Some of the recent profitability is cyclical. When rates fall, the gap between what banks earn on loans and securities and what they pay on deposits typically narrows, although the timing depends on how quickly deposit costs adjust.

But JPMorgan also has strengths that do not depend on the rate cycle: a leading investment bank, a large wealth and asset management business, and a payments franchise that earns fees from corporate clients. Lower rates can also help by supporting loan demand and capital markets activity.

We are neutral. JPMorgan is the highest-quality large US bank in our view, but its shares already reflect that. Investors should expect net interest income to soften in a falling-rate environment and judge the bank by how well fee businesses offset it.""",
    },
    {
        "key": "jpm-002", "symbol": "JPM", "name": "JPMorgan Chase & Co.", "sentiment": "bullish", "topics": ["management", "competition"], "days_ago": 17,
        "title": "Scale is becoming JPMorgan's biggest advantage as banking technology costs rise",
        "body": """Banking is increasingly a technology business. Fraud prevention, digital apps, payments and regulatory compliance all require large and continuing investment, and those costs do not fall in proportion for smaller institutions.

JPMorgan spends more on technology than most banks earn in revenue, and it can spread that spending across a very large customer base. That makes each new product cheaper per customer than it would be for a regional bank, and it helps explain JPMorgan's steady gains in deposits and card spending.

Its balance sheet strength also matters. During periods of stress, depositors and corporate clients tend to move money toward the institutions they see as safest, which has repeatedly benefited JPMorgan.

The main risks are management succession at some point and the regulatory capital burden that comes with being the largest US bank. But we think the competitive gap between JPMorgan and smaller banks is more likely to widen than narrow.""",
    },
    # ------------------------------------------------------------------ Visa
    {
        "key": "v-001", "symbol": "V", "name": "Visa Inc.", "sentiment": "bullish", "topics": ["growth", "revenue"], "days_ago": 55,
        "title": "Cross-border spending is the swing factor in Visa's growth",
        "body": """Visa earns a fee on almost every card transaction on its network, but cross-border transactions, where a card issued in one country is used in another, carry noticeably higher fees. That makes international travel and online shopping across borders unusually important to its profits.

When travel is strong, cross-border revenue grows faster than overall payment volumes and lifts margins. When it slows, growth can fall back toward the more modest pace of domestic spending.

The longer-term case does not depend only on travel. Cash is still widely used in many countries, and the shift to card and digital payments continues, especially in emerging markets. Visa also earns more from value-added services such as fraud prevention and data analytics.

The risk to this view is a slowdown in consumer spending, particularly discretionary travel. But Visa's business requires little capital and benefits from inflation, since fees are tied to the value of purchases. We see it as a steady compounder with cross-border volumes as the upside variable.""",
    },
    {
        "key": "v-002", "symbol": "V", "name": "Visa Inc.", "sentiment": "bearish", "topics": ["regulation", "risk"], "days_ago": 13,
        "title": "Interchange regulation remains the long-running risk for Visa",
        "body": """Visa's profits depend on fees that merchants ultimately pay when customers use cards. Merchants have been pushing back for years through lawsuits and lobbying, and lawmakers in the US have repeatedly proposed rules to increase competition on card routing.

Visa does not set interchange fees directly, since those go mainly to card-issuing banks, but rules that let merchants route transactions over cheaper networks would reduce Visa's own network fees and its bargaining position.

There is also a slower technological risk. Account-to-account payment systems backed by governments and central banks are gaining ground in several countries, offering instant transfers at low cost. In some markets these have taken a meaningful share of digital payments.

None of these threats is likely to change Visa's business quickly, and the company has shown it can adapt and partner with new systems. But its valuation assumes very durable fees, and we think the regulatory and competitive pressure is building slowly enough that markets tend to underestimate it.""",
    },
    # ------------------------------------------------------------------ Mastercard
    {
        "key": "ma-001", "symbol": "MA", "name": "Mastercard Incorporated", "sentiment": "neutral", "topics": ["valuation", "growth"], "days_ago": 36,
        "title": "Mastercard's premium valuation assumes consumer spending keeps growing steadily",
        "body": """Mastercard has been one of the most consistent compounders in financial services, and the market prices it accordingly. Its valuation sits well above the broader market, reflecting high margins, low capital needs and long-term growth in digital payments.

That premium is justified as long as payment volumes keep growing. The business has few direct costs tied to transactions, so revenue growth translates efficiently into profit growth.

The risk is that the valuation leaves little margin for a slowdown. Mastercard is exposed to consumer spending and to cross-border travel, both of which can weaken in a downturn. In that scenario, earnings would still grow, but more slowly, and the multiple could compress.

We are neutral. We would not argue that Mastercard is a poor business; it is an excellent one. But at current levels the investment case depends on the economy cooperating, and the stock offers less protection than its quality suggests.""",
    },
    {
        "key": "ma-002", "symbol": "MA", "name": "Mastercard Incorporated", "sentiment": "bullish", "topics": ["products", "growth"], "days_ago": 3,
        "title": "Value-added services are making Mastercard less dependent on transaction volumes",
        "body": """A growing share of Mastercard's revenue comes from services that sit on top of its payment network: fraud detection, cyber security, data analytics, loyalty programmes and consulting for banks and merchants.

These services grow faster than core payment revenue and deepen Mastercard's relationships with the banks that issue its cards. A bank that relies on Mastercard's fraud tools and analytics has more reasons to stay on the network when contracts come up for renewal.

They also diversify the business. Fraud prevention and security are needed whether consumer spending is strong or weak, which makes this revenue less cyclical than transaction fees.

The challenge is that some of these services compete with specialist technology companies, and margins can be lower than in the core network. But we think the strategy strengthens Mastercard's position against new payment methods, because it turns the network into a broader platform that is harder to replace.""",
    },
    # ------------------------------------------------------------------ Bitcoin
    {
        "key": "btc-001", "symbol": "BTC-USD", "name": "Bitcoin USD", "sentiment": "bullish", "topics": ["macro", "growth"], "days_ago": 76,
        "title": "Spot ETFs changed who owns bitcoin, and that is changing how it trades",
        "body": """The approval of spot bitcoin ETFs in the US opened the asset to investors who could not or would not hold it directly: pension funds, wealth managers and ordinary brokerage accounts. These funds have accumulated large holdings.

The bullish argument is about the investor base. Allocations from financial advisers and institutions tend to be slower-moving and more strategic than speculative trading on crypto exchanges. If bitcoin becomes a standard small allocation in diversified portfolios, demand could be steadier than in previous cycles.

The new structure also connects bitcoin more tightly to traditional markets. ETF flows respond to interest rates, risk appetite and portfolio rebalancing, which means bitcoin may increasingly move with other financial assets.

The risk is that ETF holders can sell just as easily as they buy, and that a period of outflows could add to volatility. Bitcoin also produces no cash flows, so its price depends entirely on demand. But we think broader ownership is a structural positive for its long-term role.""",
    },
    {
        "key": "btc-002", "symbol": "BTC-USD", "name": "Bitcoin USD", "sentiment": "bearish", "topics": ["risk", "macro"], "days_ago": 29,
        "title": "Bitcoin still behaves like a risk asset when liquidity tightens",
        "body": """Bitcoin is often described as digital gold, a hedge against inflation and monetary debasement. Its actual behaviour during stress has been less consistent with that description. In periods when interest rates rose or markets sold off, bitcoin has generally fallen alongside technology stocks, often by more.

That matters for anyone holding it as a portfolio hedge. An asset that falls when everything else falls does not provide much protection, however strong the long-term argument for scarcity.

Part of the explanation is ownership. Leveraged traders and momentum investors still account for much of the trading, and liquidations in crypto markets can amplify price moves. ETF ownership may reduce this over time, but it has not removed it.

We are cautious in the near term. Bitcoin can deliver large gains in supportive conditions, but investors should size positions for drawdowns that have historically been severe, and not assume it will hold up in a broad risk-off market.""",
    },
    {
        "key": "btc-003", "symbol": "BTC-USD", "name": "Bitcoin USD", "sentiment": "neutral", "topics": ["technicals", "macro"], "days_ago": 1,
        "title": "Bitcoin's short-term moves are increasingly tied to ETF flows",
        "body": """Daily flows into and out of the US spot bitcoin ETFs have become one of the most-watched indicators in crypto markets. Large inflows tend to coincide with rising prices and outflows with weakness, and traders now react to the flow data almost as much as to price charts.

This is a change from earlier cycles, when exchange order books and on-chain activity told most of the story. ETF flows reflect decisions by a different group of investors, who respond to interest rates, equity market sentiment and portfolio rebalancing.

For investors, this has two implications. It makes bitcoin's short-term direction somewhat more predictable from macro conditions, and it means the asset is likely to trade more in line with broader risk appetite.

We are neutral. Flow data is useful for understanding short-term moves, but it is also reactive: investors buy after prices rise and sell after they fall. It tells you about momentum, not about value.""",
    },
    # ------------------------------------------------------------------ Ethereum
    {
        "key": "eth-001", "symbol": "ETH-USD", "name": "Ethereum USD", "sentiment": "neutral", "topics": ["products", "competition"], "days_ago": 57,
        "title": "Layer-2 growth is good for Ethereum's users but complicates the fee argument",
        "body": """Ethereum's scaling strategy relies on layer-2 networks that process transactions cheaply and settle them on the main chain. That approach has worked technically: transaction costs for users have fallen sharply, and activity has moved onto these networks.

The complication is economic. A popular argument for owning ether was that heavy network use would generate fees, and a portion of those fees is removed from supply. When activity shifts to layer-2s that pay relatively little to the main chain, fee revenue on Ethereum itself can fall even as total usage rises.

Supporters argue that this is a deliberate trade-off: cheaper transactions grow the ecosystem, and the main chain remains the trusted settlement layer for a much larger economy. Over time, higher settlement demand could restore fee income.

We are neutral because both readings are plausible. Ethereum's developer ecosystem and position in stablecoins and tokenisation remain strong, but the link between usage and value for ether holders is less direct than it once appeared.""",
    },
    {
        "key": "eth-002", "symbol": "ETH-USD", "name": "Ethereum USD", "sentiment": "bearish", "topics": ["competition", "valuation"], "days_ago": 14,
        "title": "Ethereum faces more competition for on-chain activity than in the last cycle",
        "body": """In the previous crypto cycle, Ethereum was the default platform for decentralised finance, NFTs and new token launches. Today, faster and cheaper blockchains compete for the same activity, and some have attracted significant trading volumes, developers and users.

Ethereum's response, pushing activity to layer-2 networks, keeps users within its broader ecosystem but fragments liquidity across many chains. Competitors that offer a single fast chain can be simpler for users and developers.

This matters for valuation. If ether's value depends on being the main venue for on-chain economic activity, then losing share to rivals weakens that case, even if the overall crypto market grows.

We remain cautious. Ethereum's security, decentralisation and institutional adoption, including spot ETFs, are real advantages. But the market tends to price it as the obvious long-term winner, and we think the competitive landscape is more open than that.""",
    },
    # ------------------------------------------------------------------ Emaar
    {
        "key": "emaar-001", "symbol": "EMAAR.AE", "name": "Emaar Properties PJSC", "sentiment": "bullish", "topics": ["real_estate", "growth"], "days_ago": 69,
        "title": "Emaar's sales backlog gives it a level of visibility most developers lack",
        "body": """Emaar sells most of its residential projects off-plan, with buyers paying in instalments during construction. That creates a large backlog of contracted sales that will be recognised as revenue over the next several years.

The size of this backlog is one of the most useful numbers for understanding Emaar. It means a significant part of future revenue is already sold, which reduces uncertainty about the next few years of earnings even if new sales slow.

Dubai's property market has been supported by population growth, long-term residency schemes and strong international demand, and Emaar's master-planned communities and brand have allowed it to launch projects that sell quickly.

The risk is that off-plan markets can turn quickly when sentiment changes, and that a slowdown in new sales would show up in the backlog with a delay. But the existing backlog and Emaar's strong balance sheet give it a cushion that smaller developers do not have.""",
    },
    {
        "key": "emaar-002", "symbol": "EMAAR.AE", "name": "Emaar Properties PJSC", "sentiment": "neutral", "topics": ["real_estate", "rates"], "days_ago": 34,
        "title": "Dubai demand is strong, but the supply pipeline is the variable to watch for Emaar",
        "body": """Demand for Dubai property has been driven by population growth, inflows of wealthy residents and investors, and the city's position as a regional business hub. Those drivers remain in place.

The question is supply. Developers across Dubai have launched a large number of new projects, and many are due for completion over the next few years. If handovers outpace population growth, prices and rents could come under pressure, particularly in segments popular with investors rather than end users.

Emaar is better placed than most to handle a softer market. Its brand and prime locations tend to hold value, and it has a recurring income business in malls and hotels. But its share price still responds to sentiment about the wider market.

Interest rates are the other factor. Because the dirham is pegged to the dollar, UAE mortgage rates follow US rates, so a lower-rate environment would support affordability. We are neutral: the fundamentals are healthy, but the supply cycle deserves close attention.""",
    },
    {
        "key": "emaar-003", "symbol": "EMAAR.AE", "name": "Emaar Properties PJSC", "sentiment": "bullish", "topics": ["revenue", "dividends"], "days_ago": 7,
        "title": "Malls and hotels make Emaar more than a property-development cycle",
        "body": """Emaar is usually discussed as a developer, but it also owns and operates some of Dubai's most visited retail and hospitality assets, including the Dubai Mall and a portfolio of hotels.

These businesses produce recurring income from rents, footfall and room revenue. They benefit from tourism and the growth of Dubai's population rather than from property sales, which gives Emaar a source of earnings that is less exposed to the development cycle.

That recurring income also supports shareholder returns. A steadier cash flow base makes it easier for the company to pay dividends through periods when development sales are slower.

Tourism can be affected by regional events and global travel conditions, and new malls compete for shoppers. But we think the market often treats Emaar too much as a cyclical developer, and that its recurring assets deserve a higher weight in the valuation.""",
    },
    # ------------------------------------------------------------------ Emirates NBD
    {
        "key": "enbd-001", "symbol": "EMIRATESNBD.AE", "name": "Emirates NBD Bank PJSC", "sentiment": "neutral", "topics": ["rates", "earnings"], "days_ago": 50,
        "title": "Because of the dirham peg, Emirates NBD's margins follow US interest rates",
        "body": """The UAE dirham is pegged to the US dollar, so the UAE central bank broadly follows changes in US interest rates. For banks like Emirates NBD, that means the Federal Reserve has a major influence on profitability.

Higher rates have been good for UAE banks, which hold large balances of low-cost or interest-free deposits. As rates rose, the income from loans and securities increased much faster than deposit costs, and net interest margins widened.

The reverse applies when rates fall. Margins are likely to narrow, and banks will need loan growth and fee income to keep profits rising. The UAE economy has been growing strongly, which supports lending, but loan growth rarely offsets margin compression fully in the short term.

We are neutral. Emirates NBD is well capitalised and benefits from Dubai's growth, but investors should read US rate expectations as one of the main drivers of its earnings rather than a distant macro factor.""",
    },
    {
        "key": "enbd-002", "symbol": "EMIRATESNBD.AE", "name": "Emirates NBD Bank PJSC", "sentiment": "bullish", "topics": ["growth", "deals"], "days_ago": 10,
        "title": "International expansion gives Emirates NBD growth beyond the UAE, with execution risk attached",
        "body": """Emirates NBD has built a meaningful presence outside the UAE, including its Turkish subsidiary and operations in Egypt, Saudi Arabia and other markets. These businesses give it access to larger populations and faster loan growth than the domestic market alone.

The bullish case is diversification. The UAE banking market is competitive and relatively mature, so international operations offer a second source of growth. When they perform well, they can lift group returns significantly.

The trade-off is risk. Several of these markets have more volatile currencies, higher inflation and less predictable regulation. Turkey in particular has delivered strong profits in some years and large currency losses in others.

We lean positive because Emirates NBD has shown discipline in how it manages these exposures and has a strong domestic franchise to fall back on. But investors should expect international results to be noisier than the UAE business and judge them over a full cycle rather than quarter by quarter.""",
    },
    # ------------------------------------------------------------------ First Abu Dhabi Bank
    {
        "key": "fab-001", "symbol": "FAB.AD", "name": "First Abu Dhabi Bank", "sentiment": "neutral", "topics": ["valuation", "dividends"], "days_ago": 63,
        "title": "FAB's appeal is balance-sheet strength and dividends rather than fast growth",
        "body": """First Abu Dhabi Bank is the largest bank in the UAE by assets and is closely linked to the Abu Dhabi government and its related entities. That gives it a stable funding base, strong capital and a client list that includes many of the region's largest companies.

For investors, those qualities usually translate into steady dividends and lower volatility rather than rapid growth. FAB's size makes it harder to grow loans as quickly as smaller competitors, and much of its corporate lending is competitively priced.

Valuation reflects this. FAB has often traded at a modest premium to its book value, which is reasonable for its quality but does not imply large upside unless returns on equity improve.

We are neutral. FAB suits investors looking for income and stability within UAE equities. Those looking for faster earnings growth may find better opportunities among banks with more exposure to retail lending or faster-growing markets.""",
    },
    {
        "key": "fab-002", "symbol": "FAB.AD", "name": "First Abu Dhabi Bank", "sentiment": "bearish", "topics": ["rates", "margins"], "days_ago": 21,
        "title": "Falling rates are a headwind for FAB's net interest margin",
        "body": """Like other UAE banks, FAB benefited from the period of high US interest rates, which flow through to the UAE because of the dirham peg. Its large base of deposits allowed it to earn more on assets while paying relatively little more on funding.

As rates decline, that benefit reverses. FAB's loan book has a high share of floating-rate corporate lending, which reprices downward quickly when benchmark rates fall, while deposit costs may adjust more slowly.

FAB can offset some of this through growth in fees, trading income and wealth management, and through lending to large government-related projects. But margin compression usually hits earnings before those offsets show up.

We are cautious on the near-term earnings trajectory. The bank's quality is not in question, but the rate cycle is likely to be a headwind, and we would expect earnings growth to slow while rates are falling.""",
    },
    # ------------------------------------------------------------------ Aldar
    {
        "key": "aldar-001", "symbol": "ALDAR.AD", "name": "Al Dar Properties", "sentiment": "bullish", "topics": ["real_estate", "growth"], "days_ago": 46,
        "title": "Aldar is growing its recurring income alongside its development pipeline",
        "body": """Aldar is Abu Dhabi's leading property developer, but it has also been building a large portfolio of investment properties, including offices, retail, hotels, schools and logistics assets. That recurring income base has grown through both development and acquisitions.

This balance is the core of the bullish case. Development sales can be strong but cyclical; rental and operating income is steadier. Combining the two gives Aldar more consistent earnings than a pure developer and supports regular dividends.

Abu Dhabi's property market has also attracted more international buyers in recent years, and Aldar has expanded beyond the emirate into Dubai and other markets, broadening its sales base.

The risks are the usual ones for property companies: a slowdown in sales if sentiment turns, and higher borrowing costs if rates stay elevated. Expansion into new markets also brings execution risk. But we think Aldar's mix of businesses makes it one of the more resilient ways to own UAE real estate.""",
    },
    {
        "key": "aldar-002", "symbol": "ALDAR.AD", "name": "Al Dar Properties", "sentiment": "bearish", "topics": ["real_estate", "risk"], "days_ago": 4,
        "title": "International buyer demand is the part of Aldar's sales that could cool first",
        "body": """A growing share of Aldar's off-plan sales has come from overseas buyers and UAE residents who are not nationals. That has broadened demand and helped the company launch projects at a faster pace.

It also introduces a sensitivity. International and investor buyers tend to respond more to global conditions, currency movements and comparisons with other markets than end users who buy homes to live in. If sentiment toward Gulf property cools or competing markets look more attractive, this demand could slow quickly.

Aldar's recurring income portfolio and strong balance sheet mean a slowdown would not threaten the company. But development profits have been a large driver of recent earnings growth, so slower sales would show up in results.

We are cautious on the near-term outlook for development sales rather than on Aldar's long-term position. The signal to watch is the share of sales coming from end users versus investors in new launches.""",
    },
    # ------------------------------------------------------------------ ADNOC Gas
    {
        "key": "adnocgas-001", "symbol": "ADNOCGAS.AD", "name": "ADNOC Gas plc", "sentiment": "bullish", "topics": ["dividends", "energy"], "days_ago": 58,
        "title": "ADNOC Gas is built for steady dividends more than commodity upside",
        "body": """ADNOC Gas processes and markets natural gas in the UAE, supplying domestic customers and exporting gas and LNG. A large part of its business runs under long-term agreements with its parent company and other buyers, which gives it more stable revenue than a typical commodity producer.

That stability supports its main attraction for investors: a large and growing dividend. The company has set out a dividend policy designed to increase payouts over time, backed by long-term growth in gas demand in the UAE and abroad.

Gas also plays an important role in the energy transition, as a lower-emission fuel than coal or oil for power generation and industry, and the company has a pipeline of expansion projects.

The trade-off is that the structure limits upside when energy prices spike, and the free float is small, which affects liquidity. But for income-focused investors in UAE equities, we think ADNOC Gas offers an unusually predictable cash return.""",
    },
    {
        "key": "adnocgas-002", "symbol": "ADNOCGAS.AD", "name": "ADNOC Gas plc", "sentiment": "neutral", "topics": ["energy", "macro"], "days_ago": 18,
        "title": "Long-term contracts limit both the upside and the downside for ADNOC Gas",
        "body": """Investors sometimes treat ADNOC Gas like other energy stocks and expect it to move with global gas prices. Its structure makes that relationship weaker than it looks.

Much of its domestic business is priced under long-term agreements, which protects earnings when prices fall but also caps the benefit when they rise. Export sales and LNG have more direct price exposure, so they are the part of the business that responds to global markets.

This makes ADNOC Gas closer to an infrastructure business with some commodity exposure than to a pure gas producer. Earnings should be steadier than peers', and the investment case rests on volume growth from new projects and the dividend rather than on price moves.

We are neutral. The company's predictability is a strength, but investors looking for leverage to higher energy prices will find more of it elsewhere. Those who own it should focus on project execution and dividend growth.""",
    },
]  # fmt: skip
