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
