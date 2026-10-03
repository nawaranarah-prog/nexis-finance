import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./services/api";
import type { AnyObj } from "./types/api";

export type Lang = "en" | "ar";

/** Arabic for the app's interface. Keys are the English text; anything missing falls back to English. */
const AR: Record<string, string> = {
  // navigation groups & pages
  "Discover": "استكشف", "Home": "الرئيسية", "Finstagram": "فينستغرام", "AI Advisor": "المستشار الذكي",
  "UAE & Global Markets": "أسواق الإمارات والعالم", "Compare & Reports": "المقارنات والتقارير", "Valuation (IB)": "التقييم (مصرفي استثماري)",
  "Research workspace": "مساحة البحث", "Research Overview": "نظرة عامة على البحث", "Connect & Understand": "الربط والفهم",
  "Connections": "الاتصالات", "Financial Intelligence": "الذكاء المالي", "Portfolio X-Ray": "أشعة المحفظة", "Transactions": "المعاملات",
  "Intelligence Graph": "مخطط الذكاء", "Reconciliation": "المطابقة", "Economic & Filings": "الاقتصاد والإفصاحات", "Data": "البيانات",
  "Market Data": "بيانات السوق", "Data Quality": "جودة البيانات", "Portfolio & Risk": "المحفظة والمخاطر", "Asset Research": "أبحاث الأصول",
  "Portfolio Lab": "مختبر المحافظ", "Risk Analytics": "تحليل المخاطر", "Stress Testing": "اختبارات الضغط", "Factor Analytics": "تحليل العوامل",
  "Quant Research": "الأبحاث الكمية", "Quant Strategies": "الاستراتيجيات الكمية", "Backtesting": "الاختبار التاريخي", "Machine Learning": "تعلم الآلة",
  "Regime Analysis": "تحليل الأنظمة", "Anomaly Detection": "كشف الشذوذ", "Research Experiments": "تجارب البحث", "Research Assistant": "مساعد البحث",
  "Output": "المخرجات", "Reports": "التقارير", "Platform": "المنصة", "Data Lineage": "مسار البيانات", "Audit Log": "سجل التدقيق",
  "Developer API": "واجهة المطورين", "System Health": "حالة النظام", "Settings": "الإعدادات",
  // top bar, tab bar, account menu
  "Search or jump to…": "ابحث أو انتقل إلى…", "Sign in": "تسجيل الدخول", "Sign out": "تسجيل الخروج", "Markets": "الأسواق",
  "Advisor": "المستشار", "Me": "حسابي", "My Finstagram profile": "ملفي على فينستغرام", "Saved posts": "المنشورات المحفوظة",
  "Account settings": "إعدادات الحساب",
  // home
  "Your UAE investing hub": "مركزك للاستثمار في الإمارات", "Welcome back": "مرحبًا بعودتك", ", ": "، ",
  "Research UAE and global markets, ask an AI advisor, and follow what investors are saying.": "ابحث في أسواق الإمارات والعالم، واسأل المستشار الذكي، وتابع ما يقوله المستثمرون.",
  "Search any UAE stock, bond or sukuk — or any global market…": "ابحث عن أي سهم أو سند أو صك إماراتي — أو أي سوق عالمي…",
  "Compare UAE banks": "قارن البنوك الإماراتية", "UAE property stocks": "أسهم العقارات الإماراتية", "Stocks vs UAE bonds": "الأسهم مقابل السندات الإماراتية",
  "Value Aldar": "قيّم الدار", "UAE bonds & sukuk": "السندات والصكوك الإماراتية", "DFM General": "مؤشر سوق دبي العام",
  "FTSE ADX General": "مؤشر فوتسي سوق أبوظبي العام", "USD / AED": "دولار / درهم", "US 10-year yield": "عائد السندات الأمريكية لعشر سنوات",
  "Brent crude": "خام برنت", "Gold": "الذهب", "Open": "فتح", "Ask": "اسأل", "Open feed": "افتح الخلاصة", "Open Finstagram": "افتح فينستغرام",
  "Ask like you would ask a private banker. It checks live prices, news, analyst ratings and valuations before it answers.":
    "اسأل كما تسأل مصرفيك الخاص. يتحقق من الأسعار المباشرة والأخبار وتقييمات المحللين قبل أن يجيب.",
  "UAE movers today": "الأكثر تحركًا في الإمارات اليوم", "Top gainers": "الأكثر ارتفاعًا", "Biggest decliners": "الأكثر انخفاضًا",
  "Loading the latest posts…": "جارٍ تحميل أحدث المنشورات…",
  "e.g. Should I buy FAB or ADCB for dividends?": "مثلًا: هل أشتري بنك أبوظبي الأول أم التجاري للتوزيعات؟",
  "Which UAE banks look cheapest?": "أي البنوك الإماراتية تبدو الأرخص؟", "Is Aldar a good buy now?": "هل سهم الدار شراء جيد الآن؟",
  "What UAE bonds or sukuk yield the most?": "ما السندات أو الصكوك الإماراتية الأعلى عائدًا؟", "All UAE shares": "كل الأسهم الإماراتية",
  "Market data from public sources, delayed. Educational tools — not personalised financial advice.": "بيانات السوق من مصادر عامة ومتأخرة. أدوات تعليمية — وليست نصيحة مالية شخصية.",
  // finstagram
  "For you": "لك", "Following": "متابَع", "Trending": "الرائج", "Saved": "المحفوظ", "Follow": "متابعة", "News page": "صفحة أخبار",
  "Search stocks, pages, people, #tags": "ابحث عن أسهم وصفحات وأشخاص و#وسوم", "Post": "نشر", "Posting…": "جارٍ النشر…", "📷 Photo": "📷 صورة",
  "Share an idea, a chart or a trade thesis… use $EMAAR.AE and #tags": "شارك فكرة أو رسمًا بيانيًا أو رؤية استثمارية… استخدم $EMAAR.AE و#الوسوم",
  "Save": "حفظ", "Remove from saved": "إزالة من المحفوظ", "Suggest more like this": "اقترح المزيد من هذا", "Suggest less like this": "اقترح أقل من هذا",
  "Open original ↗": "افتح المصدر الأصلي ↗", "Go to post": "اذهب إلى المنشور", "Copy link": "نسخ الرابط", "Report": "إبلاغ", "Delete": "حذف",
  "Ask the AI advisor about this": "اسأل المستشار الذكي عن هذا", "Ask AI": "اسأل الذكاء", "Add a comment…": "أضف تعليقًا…", "Sign in to comment": "سجّل الدخول للتعليق",
  "News pages to follow": "صفحات أخبار للمتابعة", "Trending tickers · 7d": "الأسهم الرائجة · ٧ أيام", "Hashtags": "الوسوم",
  "Edit profile": "تعديل الملف الشخصي", "posts": "منشورات", "following": "متابَعون", "likes": "إعجابات", "like": "إعجاب",
  "Join Finstagram to post ideas and charts, save posts, follow stocks and news pages, and tune your feed.":
    "انضم إلى فينستغرام لنشر الأفكار والرسوم، وحفظ المنشورات، ومتابعة الأسهم وصفحات الأخبار، وتخصيص خلاصتك.",
  // advisor
  "AI Financial Advisor": "المستشار المالي الذكي", "What would you like to know?": "ماذا تريد أن تعرف؟", "Send": "إرسال", "New chat": "محادثة جديدة",
  "Thinking…": "يفكر…", "Ask anything — e.g. What's up with Emaar? I want to buy 500 shares": "اسأل أي شيء — مثلًا: ما أخبار إعمار؟ أريد شراء ٥٠٠ سهم",
  "About this Finstagram post": "عن منشور فينستغرام هذا", "Remove": "إزالة",
  "Explain this simply": "اشرح هذا ببساطة", "Is this good or bad news for investors?": "هل هذا خبر جيد أم سيئ للمستثمرين؟",
  "Should I buy or sell because of this?": "هل يجب أن أشتري أو أبيع بسبب هذا؟", "Which UAE stocks does this affect?": "ما الأسهم الإماراتية التي يؤثر عليها هذا؟",
  // login
  "Welcome back.": "مرحبًا بعودتك.", "Create your account": "أنشئ حسابك", "Email": "البريد الإلكتروني", "Phone number": "رقم الهاتف",
  "Email or username": "البريد الإلكتروني أو اسم المستخدم", "Mobile number": "رقم الجوال", "Password": "كلمة المرور", "Create account": "إنشاء حساب",
  "New here?": "جديد هنا؟", "Create an account": "أنشئ حسابًا", "Already have an account?": "لديك حساب بالفعل؟", "Continue with Google": "المتابعة باستخدام Google",
  "Continue without an account →": "المتابعة بدون حساب ←",
  "Sign in to post, save, follow and get a feed tuned to you.": "سجّل الدخول لتنشر وتحفظ وتتابع وتحصل على خلاصة مخصصة لك.",
  "One account for the whole site — the advisor, reports and Finstagram.": "حساب واحد للموقع كله — المستشار والتقارير وفينستغرام.", "Send me a code": "أرسل لي رمزًا", "Verify and continue": "تحقق وتابع",
  // settings
  "Account": "الحساب", "Preferences": "التفضيلات", "Language": "اللغة", "Theme": "المظهر", "System": "النظام", "Light": "فاتح", "Dark": "داكن",
  "Display name": "الاسم الظاهر", "Username": "اسم المستخدم", "Save changes": "حفظ التغييرات", "Change password": "تغيير كلمة المرور",
  "Set a password": "تعيين كلمة مرور", "Current password": "كلمة المرور الحالية", "New password": "كلمة المرور الجديدة",
  "Security": "الأمان", "Sign out of all other devices": "تسجيل الخروج من جميع الأجهزة الأخرى", "Delete account": "حذف الحساب",
  "Danger zone": "منطقة الخطر", "Sign in to manage your account": "سجّل الدخول لإدارة حسابك", "Changes saved": "تم حفظ التغييرات",
  // navigation groups
  "Research": "الأبحاث", "Portfolio": "المحفظة", "Intelligence": "الذكاء", "Quant": "الكمّي", "Valuation": "التقييم",
  "Research software · not investment advice": "برنامج أبحاث · ليس نصيحة استثمارية",
  // home
  "Understand the move before you make one.": "افهم حركة السوق قبل أن تتحرك.",
  "Search any ADX or DFM share, UAE bond or global market — or ask a research question. The advisor checks live prices, news and valuations, and shows its working.":
    "ابحث عن أي سهم في سوق أبوظبي أو دبي، أو سند إماراتي، أو أي سوق عالمي — أو اطرح سؤالًا بحثيًا. يتحقق المستشار من الأسعار المباشرة والأخبار والتقييمات، ويعرض خطواته.",
  "Search a ticker or ask a question — e.g. Compare FAB and Emirates NBD": "ابحث عن رمز أو اطرح سؤالًا — مثلًا: قارن بين أبوظبي الأول والإمارات دبي الوطني",
  // pulse discussions
  "Pulse": "النبض", "Community": "المجتمع", "Open Community": "افتح المجتمع", "News and posts from the Community": "أخبار ومنشورات من المجتمع",
  "See what investors are saying.": "اعرف ماذا يقول المستثمرون.", "What investors on Nexis are saying": "ماذا يقول المستثمرون على نكسس",
  "Every Pulse comes from discussions written by Nexis members — the arguments for and against, the topics people care about, and an overall sentiment score.":
    "كل نبض مبني على نقاشات كتبها أعضاء نكسس — الحجج المؤيدة والمعارضة، والمواضيع التي تهم الناس، ومؤشر عام للمزاج.",
  "Search an asset — Tesla, NVDA, Emirates NBD…": "ابحث عن أصل — تسلا، NVDA، الإمارات دبي الوطني…",
  "No discussions yet": "لا توجد نقاشات بعد", "Start a discussion": "ابدأ نقاشًا", "New discussion": "نقاش جديد", "Start a discussion about any asset…": "ابدأ نقاشًا عن أي أصل…",
  "What do you think about": "ما رأيك في", "Pulse unavailable": "النبض غير متاح", "Not enough discussion data yet.": "لا توجد بيانات نقاش كافية بعد.",
  "Based on": "بناءً على", "discussions from the last": "نقاشًا خلال آخر", "days": "يومًا", "classified by AI": "صنّفها الذكاء الاصطناعي",
  "Sentiment of Nexis discussions — not a price prediction, a probability of the price rising, or financial advice.":
    "مزاج نقاشات نكسس — وليس توقعًا للسعر ولا احتمالًا لارتفاعه ولا نصيحة مالية.",
  "Sentiment": "المزاج", "Neutral": "محايد", "Discussion volume": "حجم النقاش", "discussions": "نقاشات", "people": "أشخاص", "this week": "هذا الأسبوع",
  "vs the previous week": "مقارنة بالأسبوع السابق", "Trending topics": "المواضيع الرائجة", "No topics tagged yet.": "لا مواضيع موسومة بعد.",
  "Bullish arguments": "حجج متفائلة", "Bearish arguments": "حجج متشائمة", "No bullish arguments yet.": "لا حجج متفائلة بعد.", "No bearish arguments yet.": "لا حجج متشائمة بعد.",
  "Summarise the discussion": "لخّص النقاش", "Discussion activity": "نشاط النقاش", "weeks": "أسابيع", "Newest": "الأحدث", "Most discussed": "الأكثر نقاشًا",
  "Load more": "تحميل المزيد", "Title": "العنوان", "Your reasoning": "حجتك", "Your sentiment": "مزاجك", "optional": "اختياري", "Topics": "المواضيع", "up to": "حتى",
  "Publish": "نشر", "Cancel": "إلغاء", "Comments": "التعليقات", "Comment": "تعليق", "Reply": "رد", "Edit": "تعديل", "edited": "معدّل",
  "Trending discussions": "النقاشات الرائجة", "Recent discussions": "أحدث النقاشات", "Trending assets": "الأصول الرائجة", "Biggest sentiment changes": "أكبر تغيّرات المزاج",
  "Discussion published": "تم نشر النقاش", "Nexis Community": "مجتمع نكسس", "Pulse discussion": "نقاش في النبض", "Open Pulse": "افتح النبض",
  "Sign in to start a discussion. Reading is open to everyone.": "سجّل الدخول لبدء نقاش. القراءة متاحة للجميع.",
  "Investor opinions from Bluesky, StockTwits and Nexis members": "آراء المستثمرين من بلوسكاي وستوكتويتس وأعضاء نكسس",
  "Collecting posts from Bluesky, StockTwits and Nexis members…": "جارٍ جمع المنشورات من بلوسكاي وستوكتويتس وأعضاء نكسس…",
  "Pick a stock, bond or crypto to read the latest public posts about it from Bluesky, StockTwits and Nexis members, with an AI summary of the arguments on each side. Every post links to the original and its author, and one click opens the live discussion on Reddit and X.":
    "اختر سهمًا أو سندًا أو عملة رقمية لقراءة أحدث المنشورات العامة عنها من بلوسكاي وستوكتويتس وأعضاء نكسس، مع ملخص بالذكاء الاصطناعي لحجج كل طرف. كل منشور يرتبط بأصله وبصاحبه، وبنقرة واحدة تفتح النقاش المباشر على ريديت وإكس.",
  "See the discussion on": "شاهد النقاش على", "Open on Bluesky": "افتح على بلوسكاي", "Open the post": "افتح المنشور", "Nexis members": "أعضاء نكسس",
  "Open public API · every market, including the UAE": "واجهة عامة مفتوحة · كل الأسواق بما فيها الإمارات",
  "Finstagram posts by members that tag the asset": "منشورات الأعضاء على فينستغرام التي تذكر الأصل",
  "Open a ticker to read what investors say about it.": "افتح رمزًا لقراءة ما يقوله المستثمرون عنه.",
  "Waiting for Reddit to approve API access — Reddit posts appear once approved": "بانتظار موافقة ريديت على الوصول — تظهر منشورات ريديت بعد الموافقة",
  "Investor opinions": "آراء المستثمرين", "What Reddit, X and StockTwits are saying": "ماذا يقول ريديت وإكس وستوكتويتس",
  "Investor opinions from Reddit, X and StockTwits": "آراء المستثمرين من ريديت وإكس وستوكتويتس",
  "What are investors saying?": "ماذا يقول المستثمرون؟", "What investors are saying": "ماذا يقول المستثمرون",
  "Pick a stock, bond or crypto to read the latest public posts about it from Reddit, X and StockTwits, with an AI summary of the arguments on each side. Every post links to the original and its author.":
    "اختر سهمًا أو سندًا أو عملة رقمية لقراءة أحدث المنشورات العامة عنها من ريديت وإكس وستوكتويتس، مع ملخص بالذكاء الاصطناعي لحجج كل طرف. كل منشور يرتبط بأصله وبصاحبه.",
  "Search a stock, bond or crypto…": "ابحث عن سهم أو سند أو عملة رقمية…", "Another stock, bond or crypto…": "سهم أو سند أو عملة رقمية أخرى…",
  "Start with": "ابدأ بـ", "UAE favourites and what Finstagram is talking about": "المفضلة في الإمارات وما يتحدث عنه فينستغرام", "Sources": "المصادر",
  "Prices, fundamentals and news": "الأسعار والأساسيات والأخبار",
  "These are opinions from public social media, not verified information. A lot of positive or negative talk is not evidence that a price will move.":
    "هذه آراء من وسائل التواصل العامة، وليست معلومات موثّقة. كثرة الحديث الإيجابي أو السلبي ليست دليلًا على أن السعر سيتحرك.",
  "Collecting posts from Reddit, X and StockTwits…": "جارٍ جمع المنشورات من ريديت وإكس وستوكتويتس…",
  "AI summary of": "ملخص بالذكاء الاصطناعي لـ", "AI summary": "ملخص بالذكاء الاصطناعي", "Reading": "جارٍ قراءة", "posts and summarising the arguments…": "منشورًا وتلخيص الحجج…",
  "No AI summary": "لا يوجد ملخص", "Arguments for": "حجج مؤيدة", "Arguments against and risks": "حجج معارضة ومخاطر", "Open questions": "أسئلة مطروحة",
  "None raised in these posts.": "لم يُطرح شيء في هذه المنشورات.",
  "Generated by AI from the posts below and cited by number. It describes opinions; it does not check whether they are true.":
    "مولَّد بالذكاء الاصطناعي من المنشورات أدناه ومُشار إليها بالأرقام. يصف الآراء ولا يتحقق من صحتها.",
  "Posts": "المنشورات", "Newest first · searched for": "الأحدث أولًا · بحثنا عن", "All": "الكل", "Nothing from this source.": "لا شيء من هذا المصدر.",
  "No posts about this asset were found on the connected sources right now.": "لم نجد منشورات عن هذا الأصل في المصادر المتصلة حاليًا.",
  "posts collected": "منشورات تم جمعها", "Self-tagged on StockTwits": "وسوم أصحابها على ستوكتويتس", "Bullish": "متفائل", "Bearish": "متشائم",
  "of": "من", "posts tagged": "منشورات موسومة", "No self-tagged posts": "لا منشورات موسومة", "Cited in the summary": "مذكور في الملخص",
  "Chosen by the poster on StockTwits": "اختاره صاحب المنشور على ستوكتويتس", "points": "نقاط", "replies": "ردود", "on": "على",
  "Open on Reddit": "افتح على ريديت", "Open on X": "افتح على إكس", "Open on StockTwits": "افتح على ستوكتويتس", "Discuss on Nexis": "ناقش على نكسس",
  "On Reddit or X?": "لديك حساب على ريديت أو إكس؟", "Link your account so people on Nexis can see who you are when you post and comment.": "اربط حسابك ليعرف الناس على نكسس من أنت عندما تنشر وتعلّق.",
  "Link accounts": "اربط الحسابات", "How this works": "كيف يعمل هذا",
  "Nexis searches each source for the ticker and the company name, keeps posts that are about the asset, and links every post and author to the original. Counts are a sample of recent posts, not total discussion volume.":
    "يبحث نكسس في كل مصدر عن الرمز واسم الشركة، ويحتفظ بالمنشورات المتعلقة بالأصل، ويربط كل منشور وكاتبه بالأصل. الأعداد عيّنة من أحدث المنشورات وليست حجم النقاش الكلي.",
  "Collected": "جُمعت", "refreshes every 10 minutes": "تتحدث كل ١٠ دقائق",
  "Linked accounts": "الحسابات المرتبطة", "Not linked": "غير مرتبط", "Not available yet — the site owner still needs to connect it": "غير متاح بعد — يحتاج مالك الموقع إلى تفعيله",
  "Link Reddit or X to show your handle next to your name when you post and comment on Nexis, and to sign in with it. Nexis only reads your public username — it never posts for you.":
    "اربط ريديت أو إكس لإظهار اسمك هناك بجانب اسمك عندما تنشر وتعلّق على نكسس، ولتسجيل الدخول به. يقرأ نكسس اسم المستخدم العام فقط ولا ينشر نيابةً عنك.",
  "Link": "ربط", "Unlink": "إلغاء الربط", "Account unlinked": "تم إلغاء ربط الحساب", "Couldn't unlink": "تعذّر إلغاء الربط", "account linked": "تم ربط الحساب",
  "Couldn't link the account": "تعذّر ربط الحساب", "Continue with Reddit": "المتابعة باستخدام ريديت", "Continue with X": "المتابعة باستخدام إكس",
  "Ticker or question": "رمز أو سؤال","Search a market or ask a research question": "ابحث في سوق أو اطرح سؤالًا بحثيًا", "Search": "بحث", "Searching…": "جارٍ البحث…",
  "Ask the AI advisor": "اسأل المستشار الذكي", "Open in Compare & Reports": "افتح في المقارنات والتقارير", "move": "تنقّل", "open": "فتح", "close": "إغلاق",
  "Try": "جرّب", "Compare FAB and Emirates NBD": "قارن بين أبوظبي الأول والإمارات دبي الوطني", "Why did NVIDIA move today?": "لماذا تحرك سهم إنفيديا اليوم؟",
  "Is Aldar expensive vs. peers?": "هل سهم الدار مرتفع مقارنة بنظرائه؟", "UAE bonds and sukuk": "السندات والصكوك الإماراتية", "What's happening with gold?": "ماذا يحدث للذهب؟",
  "ADX & DFM in session": "سوقا أبوظبي ودبي مفتوحان", "ADX & DFM closed": "سوقا أبوظبي ودبي مغلقان",
  "Regular hours Mon–Fri 10:00–15:00 GST": "ساعات التداول الاعتيادية من الاثنين إلى الجمعة ١٠:٠٠–١٥:٠٠",
  "AI advisor online": "المستشار الذكي متصل", "uses live quotes, news, price statistics, comparisons and DCF valuations": "يستخدم الأسعار المباشرة والأخبار وإحصاءات الأسعار والمقارنات وتقييمات التدفقات النقدية",
  "AI model offline — the advisor answers with its built-in analyst engine from live data": "نموذج الذكاء الاصطناعي غير متصل — يجيب المستشار بمحرّكه التحليلي المدمج من البيانات المباشرة",
  "Checking the AI advisor…": "جارٍ التحقق من المستشار الذكي…", "Open the advisor": "افتح المستشار",
  "Markets now": "الأسواق الآن", "US 10Y Treasury": "سندات الخزانة الأمريكية لعشر سنوات", "Unavailable right now": "غير متاح حاليًا",
  "Market data could not be loaded": "تعذّر تحميل بيانات السوق", "Retry": "إعادة المحاولة",
  "Delayed quotes · UAE indices via TradingView, FX, rates and commodities via Yahoo Finance · lines show the last month of daily closes":
    "أسعار متأخرة · مؤشرات الإمارات من TradingView، والعملات والعوائد والسلع من Yahoo Finance · تُظهر الخطوط إغلاقات الشهر الأخير",
  "Market wire": "شريط الأخبار", "Latest on Finstagram": "الأحدث على فينستغرام", "News": "أخبار", "Member post": "منشور عضو",
  "The feed could not be loaded": "تعذّر تحميل الخلاصة", "No posts yet. News pages refresh daily.": "لا منشورات بعد. تُحدَّث صفحات الأخبار يوميًا.",
  "Ask the advisor what this story means": "اسأل المستشار عن معنى هذا الخبر",
  "Watchlist": "قائمة المتابعة", "Keep the stocks, bonds and indices you research in one place, with live prices and a month of history.":
    "احتفظ بالأسهم والسندات والمؤشرات التي تبحث فيها في مكان واحد، مع أسعار مباشرة وسجل شهر.",
  "Create a free account": "أنشئ حسابًا مجانيًا", "or": "أو", "sign in": "سجّل الدخول",
  "Your watchlist is empty. Follow a ticker here or on any Finstagram stock page.": "قائمة متابعتك فارغة. تابع رمزًا هنا أو من أي صفحة سهم على فينستغرام.",
  "added to your watchlist": "أُضيف إلى قائمة متابعتك", "removed from your watchlist": "أُزيل من قائمة متابعتك", "Couldn't update your watchlist": "تعذّر تحديث قائمة متابعتك",
  "Asset": "الأصل", "Last": "الأخير", "Day": "اليوم", "1 month": "شهر", "Nexis Pulse": "نبض نكسس", "Most mentioned · 7 days": "الأكثر ذكرًا · ٧ أيام",
  "Number of Finstagram posts that mention each ticker, mostly from news pages. A measure of attention — not sentiment, and not a prediction. Investor-opinion analysis from other sources is not connected yet.":
    "عدد منشورات فينستغرام التي تذكر كل رمز، ومعظمها من صفحات الأخبار. مقياس للاهتمام — وليس للمزاج العام ولا للتنبؤ. تحليل آراء المستثمرين من مصادر أخرى غير متصل بعد.",
  "UAE movers": "الأكثر تحركًا في الإمارات", "ADX and DFM shares": "سهمًا في أبوظبي ودبي", "delayed": "متأخرة",
  "Market data from public sources, delayed. AI answers are generated from that data and can be wrong. Educational tools — not personalised financial advice.":
    "بيانات السوق من مصادر عامة ومتأخرة. إجابات الذكاء الاصطناعي مولّدة من هذه البيانات وقد تكون خاطئة. أدوات تعليمية — وليست نصيحة مالية شخصية.",
};

const DICTS: Record<Lang, Record<string, string>> = { en: {}, ar: AR };

interface Ctx { lang: Lang; setLang: (l: Lang) => void; t: (s: string) => string }
const LangContext = createContext<Ctx>({ lang: "en", setLang: () => undefined, t: (s) => s });

function stored(): Lang {
  try { return localStorage.getItem("nexis.lang") === "ar" ? "ar" : "en"; } catch { return "en"; }
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<{ user: AnyObj | null }>("/auth/me"), staleTime: 60_000 });
  const [lang, setLangState] = useState<Lang>(stored);
  const account = me.data?.user?.language as Lang | undefined;
  useEffect(() => { if (account && account !== lang) setLangState(account); }, [account]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    try { localStorage.setItem("nexis.lang", lang); } catch { /* storage unavailable */ }
  }, [lang]);
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    if (me.data?.user) void api.patch("/auth/me", { language: l }).then(() => qc.invalidateQueries({ queryKey: ["me"] })).catch(() => undefined);
  }, [me.data?.user, qc]);
  const t = useCallback((s: string) => DICTS[lang][s] ?? s, [lang]);
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useT() {
  return useContext(LangContext);
}
