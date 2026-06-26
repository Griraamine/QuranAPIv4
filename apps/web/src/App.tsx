import {
  Ban,
  CheckCircle2,
  Download,
  FolderOpen,
  ImagePlus,
  LoaderCircle,
  Play,
  Search,
  Square,
  Upload
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { api } from "./api";
import type {
  BackgroundAsset,
  Chapter,
  Compatibility,
  Moshaf,
  Reciter,
  RenderJob,
  VersePreview,
  VisualStyle
} from "./types";

type PageId = "setup" | "background" | "text" | "badge" | "thumbnail" | "render";
type BackgroundMode = "single" | "slideshow";

type PersistedEditorState = {
  activePage?: PageId;
  reciterQuery?: string;
  selectedReciterId?: string;
  selectedMoshafId?: string;
  selectedChapterId?: number;
  ayahFrom?: number;
  ayahTo?: number;
  includeBismillah?: boolean;
  backgroundMode?: BackgroundMode;
  backgroundIds?: string[];
  visualStyle?: VisualStyle;
  badgeEnabled?: boolean;
  badgeArabicSurah?: string;
  englishSurahTitle?: string;
  arabicReciterTitle?: string;
  englishReciterTitle?: string;
};

const pages: Array<{ id: PageId; label: string; detail: string }> = [
  { id: "setup", label: "Setup", detail: "Reciter, surah, ayahs" },
  { id: "background", label: "Background", detail: "Media source" },
  { id: "text", label: "Text", detail: "Font and layout" },
  { id: "badge", label: "Badge", detail: "Top label" },
  { id: "thumbnail", label: "Thumbnail", detail: "Export image" },
  { id: "render", label: "Render", detail: "Output" }
];

const arabicFonts = [
  { key: "uthmanic", label: "Uthmanic", css: '"Amiri Quran", "Amiri", serif' },
  { key: "amiri", label: "Amiri", css: '"Amiri Quran", "Amiri", serif' },
  { key: "noto_naskh", label: "Noto Naskh", css: '"Noto Naskh Arabic", "Noto Sans Arabic", serif' },
  { key: "scheherazade", label: "Scheherazade", css: '"Scheherazade New", "Amiri", serif' },
  { key: "scheherazade_b", label: "Scheherazade B", css: '"Scheherazade New", "Amiri", serif' },
  { key: "lateef", label: "Lateef", css: '"Lateef", "Amiri", serif' },
  { key: "indo_pak", label: "Indo-Pak", css: '"Jameel Noori Nastaleeq", "Noto Nastaliq Urdu", serif' },
  { key: "al_mushaf", label: "Al Mushaf", css: '"Al Mushaf", "Amiri Quran", serif' },
  { key: "poetry", label: "Poetry", css: '"Arabic Typesetting", "Amiri", serif' },
  { key: "hafs_ex1", label: "Hafs Ex1", css: '"Hafs", "Amiri Quran", serif' },
  { key: "muhammadi", label: "Muhammadi", css: '"Muhammadi Quranic", "Amiri Quran", serif' },
  { key: "me_quran", label: "Me Quran", css: '"Me Quran", "Amiri Quran", serif' },
  { key: "nabi", label: "Nabi", css: '"Nabi", "Amiri", serif' },
  { key: "aref_ruqaa", label: "Aref Ruqaa", css: '"Aref Ruqaa", "Amiri", serif' },
  { key: "mirza", label: "Mirza", css: '"Mirza", "Amiri", serif' },
  { key: "reem_kufi", label: "Reem Kufi", css: '"Reem Kufi", "Noto Kufi Arabic", sans-serif' },
  { key: "harmattan", label: "Harmattan", css: '"Harmattan", "Noto Sans Arabic", sans-serif' },
  { key: "system", label: "System", css: 'system-ui, "Noto Sans Arabic", sans-serif' }
] as const;

const englishFonts = [
  { key: "system", label: "System", css: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
  { key: "georgia", label: "Georgia", css: 'Georgia, "Noto Serif", "Liberation Serif", serif' },
  { key: "palatino", label: "Palatino", css: '"Palatino Linotype", Palatino, "Book Antiqua", "Noto Serif", serif' },
  { key: "times", label: "Times", css: '"Times New Roman", Times, "Liberation Serif", serif' },
  { key: "avenir", label: "Avenir", css: 'Avenir, "Avenir Next", "Open Sans", system-ui, sans-serif' },
  { key: "didot", label: "Didot", css: 'Didot, "Bodoni 72", "Noto Serif Display", "Noto Serif", serif' }
] as const;

const arabicFontCss = (key: string) => arabicFonts.find((font) => font.key === key)?.css ?? arabicFonts[1].css;
const englishFontCss = (key: string) => englishFonts.find((font) => font.key === key)?.css ?? englishFonts[3].css;
const badgeArabicFontCss = '"SurahNameV4", "Amiri Quran", "Amiri", serif';
const badgeSurahGlyph = (chapterId: number) => String.fromCharCode(0xe000 + chapterId);
const bismillahWords = ["بسم", "الله", "الرحمن", "الرحيم"];
const murattalTokens = ["murattal", "murattel", "muratal", "مرتل", "مرتّل"];
const px = (pixels: number) => `${pixels}px`;
const EDITOR_COOKIE_NAME = "quran_video_editor_state";
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

function isMurattalMoshaf(moshaf: Moshaf) {
  const label = `${moshaf.name} ${moshaf.rewaya ?? ""}`.toLowerCase();
  return murattalTokens.some((token) => label.includes(token));
}

function defaultMoshafForReciter(reciter: Reciter | null | undefined, chapterId?: number) {
  const moshafs = reciter?.moshafs ?? [];
  if (!moshafs.length) {
    return "";
  }
  const available = chapterId
    ? moshafs.filter((moshaf) => moshaf.available_surahs.includes(chapterId))
    : [];
  const candidates = available.length ? available : moshafs;
  return (candidates.find(isMurattalMoshaf) ?? candidates[0]).id;
}

function useStageScale(width: number, height: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  useLayoutEffect(() => {
    const update = () => {
      const node = ref.current;
      if (!node) {
        return;
      }
      const bounds = node.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) {
        return;
      }
      setScale(Math.min(bounds.width / width, bounds.height / height));
    };

    update();
    if (typeof ResizeObserver !== "undefined" && ref.current) {
      const observer = new ResizeObserver(update);
      observer.observe(ref.current);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [height, width]);

  return [ref, scale] as const;
}

const defaultStyle: VisualStyle = {
  background_style: {
    dim_opacity: 35
  },
  typography: {
    arabic_font_size: 63,
    gloss_font_size: 40,
    translation_font_size: 30,
    text_shade: "#FFFFFF",
    secondary_shade: "#FFFFFF",
    outline_px: 0,
    shadow_px: 0,
    line_spacing: 1.44,
    position: "center",
    arabic_y: 470,
    gloss_y: 685,
    translation_y: 685,
    arabic_box_x: 960,
    arabic_box_y: 470,
    arabic_box_width: 1620,
    arabic_box_height: 170,
    translation_box_x: 960,
    translation_box_y: 685,
    translation_box_width: 1620,
    translation_box_height: 130,
    text_transition: "none",
    fade_duration_ms: 350,
    arabic_font_key: "amiri",
    english_font_key: "times"
  },
  badge_style: {
    x: 128,
    y: 88,
    artistic_surah_size: 43,
    english_size: 38,
    range_size: 34,
    line_gap: 10,
    shade: "#FFFFFF",
    secondary_shade: "#FFFFFF",
    show_reciter: false
  },
  thumbnail_style: {
    artistic_surah_size: 90,
    artistic_y: 285,
    artistic_shade: "#FFFFFF",
    show_english: true,
    english_size: 62,
    english_y: 402,
    english_shade: "#FFFFFF",
    shadow_px: 0
  }
};

const layoutPositions = {
  top: { arabic_box_y: 330, arabic_y: 330, translation_box_y: 560, gloss_y: 560, translation_y: 560 },
  center: { arabic_box_y: 470, arabic_y: 470, translation_box_y: 685, gloss_y: 685, translation_y: 685 },
  bottom: { arabic_box_y: 650, arabic_y: 650, translation_box_y: 870, gloss_y: 870, translation_y: 870 }
} satisfies Record<
  VisualStyle["typography"]["position"],
  Pick<
    VisualStyle["typography"],
    "arabic_box_y" | "arabic_y" | "translation_box_y" | "gloss_y" | "translation_y"
  >
>;

function normalizeStyle(style: VisualStyle): VisualStyle {
  const typography = { ...defaultStyle.typography, ...style.typography };
  const arabicBoxY = typography.arabic_box_y ?? typography.arabic_y;
  const translationBoxY = typography.translation_box_y ?? typography.translation_y;
  const textTransition =
    typography.text_transition === "fade" || typography.text_transition === "none"
      ? typography.text_transition
      : defaultStyle.typography.text_transition;
  return {
    background_style: {
      ...defaultStyle.background_style,
      ...(style.background_style ?? {})
    },
    typography: {
      ...typography,
      text_shade: "#FFFFFF",
      secondary_shade: "#FFFFFF",
      outline_px: 0,
      shadow_px: 0,
      arabic_y: arabicBoxY,
      gloss_y: translationBoxY,
      translation_y: translationBoxY,
      arabic_box_y: arabicBoxY,
      translation_box_y: translationBoxY,
      text_transition: textTransition,
      fade_duration_ms: clamp(
        finiteNumber(typography.fade_duration_ms, defaultStyle.typography.fade_duration_ms),
        0,
        3000
      ),
      arabic_font_key: typography.arabic_font_key ?? "amiri",
      english_font_key: typography.english_font_key ?? "times"
    },
    badge_style: {
      ...defaultStyle.badge_style,
      ...style.badge_style,
      shade: "#FFFFFF",
      secondary_shade: "#FFFFFF",
      show_reciter: false
    },
    thumbnail_style: {
      ...defaultStyle.thumbnail_style,
      ...style.thumbnail_style,
      artistic_shade: "#FFFFFF",
      english_shade: "#FFFFFF",
      show_english: true,
      shadow_px: 0
    }
  };
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
const finiteNumber = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;
const stringValue = (value: unknown, fallback = "") => (typeof value === "string" ? value : fallback);
const isPageId = (value: unknown): value is PageId =>
  typeof value === "string" && pages.some((page) => page.id === value);
const isBackgroundMode = (value: unknown): value is BackgroundMode => value === "single" || value === "slideshow";
const arabicIndicDigits = ["٠", "١", "٢", "٣", "٤", "٥", "٦", "٧", "٨", "٩"];

function arabicAyahNumber(value: number) {
  return String(value).replace(/\d/g, (digit) => arabicIndicDigits[Number(digit)]);
}

function normalizeArabicWord(value: string) {
  return value
    .normalize("NFC")
    .replace(/[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]/g, "")
    .replace(/ـ/g, "")
    .replace(/[ٱأإآ]/g, "ا")
    .replace(/[^\u0600-\u06ff]/g, "");
}

function stripLeadingBismillah(text: string) {
  const words = text.trim().split(/\s+/);
  if (words.length < bismillahWords.length) {
    return text;
  }
  const leading = words.slice(0, bismillahWords.length).map(normalizeArabicWord);
  if (!leading.every((word, index) => word === bismillahWords[index])) {
    return text;
  }
  return words.slice(bismillahWords.length).join(" ");
}

function compatibilityMessage(
  compatibility: Compatibility | null,
  reciterRequiresMoshaf: boolean,
  selectedMoshafId: string
) {
  if (!compatibility) {
    return reciterRequiresMoshaf && !selectedMoshafId ? "Select a moshaf / rewaya" : "Checking timing compatibility";
  }
  if (compatibility.compatible) {
    return `Compatible with ${compatibility.has_word_timing ? "word timing" : "ayah timing"}`;
  }
  switch (compatibility.status ?? compatibility.reason) {
    case "timing_unavailable":
      return compatibility.reason && compatibility.reason !== "timing_unavailable"
        ? compatibility.reason
        : "The selected provider has audio for this selection, but no usable ayah timing. Choose another reciter for exact sync.";
    case "timing_ambiguous":
      return "More than one timing source matches this selection. Choose another reciter or moshaf for exact sync.";
    case "surah_unavailable":
      return "The selected moshaf does not include this surah.";
    case "text_timing_mismatch":
      return "The Quran text and timing do not align for this selection.";
    case "audio_timing_mismatch":
      return "The audio duration and timing data do not match closely enough.";
    case "timing_invalid":
      return "The selected provider returned invalid timing data for this selection.";
    default:
      return compatibility.reason ?? "This reciter, moshaf, and surah combination is not renderable.";
  }
}

function readEditorCookie(): PersistedEditorState | null {
  if (typeof document === "undefined") {
    return null;
  }
  const cookie = document.cookie
    .split(";")
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(`${EDITOR_COOKIE_NAME}=`));
  if (!cookie) {
    return null;
  }
  try {
    const value = decodeURIComponent(cookie.slice(EDITOR_COOKIE_NAME.length + 1));
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as PersistedEditorState) : null;
  } catch {
    return null;
  }
}

function writeEditorCookie(state: PersistedEditorState) {
  if (typeof document === "undefined") {
    return;
  }
  const payload = encodeURIComponent(JSON.stringify(state));
  document.cookie = `${EDITOR_COOKIE_NAME}=${payload}; Max-Age=${COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Lax`;
}

function FitBoxText({
  text,
  className,
  fontFamily,
  baseFontSize,
  lineHeight,
  x,
  y,
  width,
  height,
  direction = "ltr",
  verticalAlign = "middle",
  italic = false,
  transitionMode = "none",
  fadeDurationMs = 350,
  ayahMarkerNumber
}: {
  text: string;
  className: string;
  fontFamily: string;
  baseFontSize: number;
  lineHeight: number;
  x: number;
  y: number;
  width: number;
  height: number;
  direction?: "ltr" | "rtl";
  verticalAlign?: "top" | "middle" | "bottom";
  italic?: boolean;
  transitionMode?: VisualStyle["typography"]["text_transition"];
  fadeDurationMs?: number;
  ayahMarkerNumber?: number;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const textRef = useRef<HTMLSpanElement | null>(null);
  const [fontSize, setFontSize] = useState(baseFontSize);

  useLayoutEffect(() => {
    const fit = () => {
      const box = boxRef.current;
      const textNode = textRef.current;
      if (!box || !textNode) {
        return;
      }
      let low = 1;
      let high = Math.max(1, Math.round(baseFontSize));
      let best = low;
      while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        textNode.style.fontSize = px(middle);
        textNode.style.lineHeight = String(lineHeight);
        const fits =
          textNode.scrollWidth <= box.clientWidth + 1 &&
          textNode.scrollHeight <= box.clientHeight + 1;
        if (fits) {
          best = middle;
          low = middle + 1;
        } else {
          high = middle - 1;
        }
      }
      textNode.style.fontSize = px(best);
      setFontSize(best);
    };

    fit();
    if (typeof ResizeObserver === "undefined" || !boxRef.current) {
      return;
    }
    const observer = new ResizeObserver(fit);
    observer.observe(boxRef.current);
    return () => observer.disconnect();
  }, [
    ayahMarkerNumber,
    baseFontSize,
    direction,
    fontFamily,
    height,
    italic,
    lineHeight,
    text,
    width,
    x,
    y
  ]);

  const textStyle = {
    fontFamily,
    fontSize: px(fontSize),
    fontStyle: italic ? "italic" : "normal",
    lineHeight,
    "--text-fade-duration": `${Math.max(0, fadeDurationMs)}ms`
  } as CSSProperties;

  return (
    <div
      className="preview-text-box"
      ref={boxRef}
      style={{
        left: px(x),
        top: px(y),
        width: px(width),
        height: px(height),
        alignItems:
          verticalAlign === "top" ? "flex-start" : verticalAlign === "bottom" ? "flex-end" : "center"
      }}
    >
      <span
        className={`preview-fit-text ${transitionMode === "fade" ? "preview-fit-text-fade" : ""} ${className}`}
        dir={direction}
        lang={direction === "rtl" ? "ar" : undefined}
        ref={textRef}
        style={textStyle}
      >
        {text}
        {ayahMarkerNumber !== undefined && (
          <span className="inline-ayah-marker" aria-label={`Ayah ${ayahMarkerNumber}`}>
            <span className="inline-ayah-marker-symbol">۝</span>
            <span className="inline-ayah-marker-number">{arabicAyahNumber(ayahMarkerNumber)}</span>
          </span>
        )}
      </span>
    </div>
  );
}

export function App() {
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [reciters, setReciters] = useState<Reciter[]>([]);
  const [backgrounds, setBackgrounds] = useState<BackgroundAsset[]>([]);
  const [activePage, setActivePage] = useState<PageId>("setup");
  const [reciterQuery, setReciterQuery] = useState("");
  const [selectedReciterId, setSelectedReciterId] = useState("");
  const [selectedMoshafId, setSelectedMoshafId] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState(1);
  const [ayahFrom, setAyahFrom] = useState(1);
  const [ayahTo, setAyahTo] = useState(1);
  const [ayahFromInput, setAyahFromInput] = useState("1");
  const [ayahToInput, setAyahToInput] = useState("1");
  const [includeBismillah, setIncludeBismillah] = useState(true);
  const [backgroundMode, setBackgroundMode] = useState<BackgroundMode>("single");
  const [backgroundIds, setBackgroundIds] = useState<string[]>([]);
  const [visualStyle, setVisualStyle] = useState<VisualStyle>(defaultStyle);
  const [styleLoaded, setStyleLoaded] = useState(false);
  const [badgeEnabled, setBadgeEnabled] = useState(true);
  const [badgeArabicSurah, setBadgeArabicSurah] = useState("");
  const [englishSurahTitle, setEnglishSurahTitle] = useState("");
  const [arabicReciterTitle, setBadgeArabicReciter] = useState("");
  const [englishReciterTitle, setEnglishReciterTitle] = useState("");
  const [compatibility, setCompatibility] = useState<Compatibility | null>(null);
  const [previewVerses, setPreviewVerses] = useState<VersePreview[]>([]);
  const [job, setJob] = useState<RenderJob | null>(null);
  const [renderSubmitting, setRenderSubmitting] = useState(false);
  const renderSubmittingRef = useRef(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const skipChapterTitleSyncRef = useRef(false);
  const skipReciterTitleSyncRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveStyle = normalizeStyle(visualStyle);
  const selectedChapter = useMemo(
    () => chapters.find((chapter) => chapter.id === selectedChapterId) ?? null,
    [chapters, selectedChapterId]
  );
  const selectedReciter = useMemo(
    () => reciters.find((reciter) => reciter.id === selectedReciterId) ?? null,
    [reciters, selectedReciterId]
  );
  const selectedMoshaf = useMemo(
    () => selectedReciter?.moshafs?.find((moshaf) => moshaf.id === selectedMoshafId) ?? null,
    [selectedMoshafId, selectedReciter]
  );
  const reciterRequiresMoshaf = Boolean(selectedReciter?.moshafs?.length);
  const availableSurahSet = useMemo(() => {
    if (!selectedMoshaf) {
      return null;
    }
    return new Set(selectedMoshaf.available_surahs);
  }, [selectedMoshaf]);
  const verseCount = selectedChapter?.verse_count ?? 1;
  const selectedBackground = backgrounds.find((background) => background.id === backgroundIds[0]) ?? null;
  const selectedBackgroundUrl = selectedBackground ? api.backgroundUrl(selectedBackground.id) : "";
  const artisticSurahName = selectedChapter?.artistic_arabic_name ?? badgeArabicSurah;
  const arabicFontFamily = arabicFontCss(effectiveStyle.typography.arabic_font_key);
  const englishFontFamily = englishFontCss(effectiveStyle.typography.english_font_key);
  const [compositionRef, compositionScale] = useStageScale(1920, 1080);
  const [thumbnailRef, thumbnailScale] = useStageScale(1280, 720);
  const pageIndex = pages.findIndex((page) => page.id === activePage);
  const filteredReciters = reciters.filter((reciter) => {
    const moshafText = reciter.moshafs?.map((moshaf) => moshaf.name).join(" ") ?? "";
    const text = `${reciter.english_name} ${reciter.arabic_name} ${reciter.style.name} ${moshafText}`.toLowerCase();
    return text.includes(reciterQuery.toLowerCase());
  });
  const visibleReciters =
    selectedReciter && !filteredReciters.some((reciter) => reciter.id === selectedReciter.id)
      ? [selectedReciter, ...filteredReciters]
      : filteredReciters;
  const previewVerse = useMemo(() => {
    const selected = previewVerses.filter(
      (verse) => verse.verse_number >= ayahFrom && verse.verse_number <= ayahTo
    );
    return selected.reduce<VersePreview | null>((longest, verse) => {
      if (!longest) {
        return verse;
      }
      return verse.text_uthmani.length > longest.text_uthmani.length ? verse : longest;
    }, null);
  }, [ayahFrom, ayahTo, previewVerses]);
  const rawPreviewArabicText =
    previewVerse?.text_uthmani || "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ";
  const basePreviewArabicText =
    includeBismillah &&
    selectedChapterId !== 1 &&
    selectedChapterId !== 9 &&
    previewVerse?.verse_number === 1
      ? stripLeadingBismillah(rawPreviewArabicText)
      : rawPreviewArabicText;
  const previewArabicText = basePreviewArabicText;
  const previewArabicAyahNumber = previewVerse?.verse_number;
  const previewTranslationText =
    previewVerse?.translation || "In the name of Allah, Most Gracious, Most Merciful.";

  const updateTypography = (patch: Partial<VisualStyle["typography"]>) => {
    setVisualStyle((current) => normalizeStyle({
      ...current,
      typography: { ...current.typography, ...patch }
    }));
  };

  const updateBadgeStyle = (patch: Partial<VisualStyle["badge_style"]>) => {
    setVisualStyle((current) => normalizeStyle({
      ...current,
      badge_style: { ...current.badge_style, ...patch }
    }));
  };

  const updateThumbnailStyle = (patch: Partial<VisualStyle["thumbnail_style"]>) => {
    setVisualStyle((current) => normalizeStyle({
      ...current,
      thumbnail_style: { ...current.thumbnail_style, ...patch }
    }));
  };

  const updateBackgroundStyle = (patch: Partial<VisualStyle["background_style"]>) => {
    setVisualStyle((current) => normalizeStyle({
      ...current,
      background_style: { ...current.background_style, ...patch }
    }));
  };

  useEffect(() => {
    Promise.all([api.chapters(), api.reciters(), api.backgrounds(), api.style()])
      .then(([chapterData, reciterData, backgroundData, styleData]) => {
        const saved = readEditorCookie();
        setChapters(chapterData);
        setReciters(reciterData);
        setBackgrounds(backgroundData);
        setVisualStyle(normalizeStyle(saved?.visualStyle ?? styleData));
        setActivePage(isPageId(saved?.activePage) ? saved.activePage : "setup");
        setReciterQuery(stringValue(saved?.reciterQuery));
        setBadgeEnabled(typeof saved?.badgeEnabled === "boolean" ? saved.badgeEnabled : true);
        setIncludeBismillah(typeof saved?.includeBismillah === "boolean" ? saved.includeBismillah : true);
        setBackgroundMode(isBackgroundMode(saved?.backgroundMode) ? saved.backgroundMode : "single");

        const selectedSavedChapter = chapterData.find((chapter) => chapter.id === saved?.selectedChapterId);
        const initialChapter = selectedSavedChapter ?? chapterData[0];
        const hasSavedChapterTitles =
          Boolean(saved?.badgeArabicSurah) || Boolean(saved?.englishSurahTitle);
        if (hasSavedChapterTitles) {
          skipChapterTitleSyncRef.current = true;
        }
        if (initialChapter) {
          const initialFrom = clamp(finiteNumber(saved?.ayahFrom, 1), 1, initialChapter.verse_count);
          const initialTo = clamp(
            finiteNumber(saved?.ayahTo, initialChapter.verse_count),
            initialFrom,
            initialChapter.verse_count
          );
          setSelectedChapterId(initialChapter.id);
          setAyahFrom(initialFrom);
          setAyahTo(initialTo);
          setBadgeArabicSurah(stringValue(saved?.badgeArabicSurah, initialChapter.arabic_name));
          setEnglishSurahTitle(stringValue(saved?.englishSurahTitle, initialChapter.english_name));
        }

        const selectedSavedReciter = reciterData.find((reciter) => reciter.id === saved?.selectedReciterId);
        const initialReciter = selectedSavedReciter ?? reciterData[0];
        const hasSavedReciterTitles =
          Boolean(saved?.arabicReciterTitle) || Boolean(saved?.englishReciterTitle);
        if (hasSavedReciterTitles) {
          skipReciterTitleSyncRef.current = true;
        }
        if (initialReciter) {
          setSelectedReciterId(initialReciter.id);
          const savedMoshaf = initialReciter.moshafs?.find(
            (moshaf) =>
              moshaf.id === saved?.selectedMoshafId &&
              (!initialChapter || moshaf.available_surahs.includes(initialChapter.id))
          );
          setSelectedMoshafId(savedMoshaf?.id ?? defaultMoshafForReciter(initialReciter, initialChapter?.id));
          setBadgeArabicReciter(stringValue(saved?.arabicReciterTitle, initialReciter.arabic_name));
          setEnglishReciterTitle(stringValue(saved?.englishReciterTitle, initialReciter.english_name));
        }

        const availableBackgroundIds = new Set(backgroundData.map((background) => background.id));
        const savedBackgroundIds = Array.isArray(saved?.backgroundIds)
          ? saved.backgroundIds.filter((id) => availableBackgroundIds.has(id))
          : [];
        setBackgroundIds(savedBackgroundIds.length ? savedBackgroundIds : backgroundData[0] ? [backgroundData[0].id] : []);
        setStyleLoaded(true);
      })
      .catch((loadError: unknown) => setError(String(loadError)));
  }, []);

  useEffect(() => () => eventSourceRef.current?.close(), []);

  useEffect(() => {
    if (!styleLoaded) {
      return;
    }
    writeEditorCookie({
      activePage,
      reciterQuery,
      selectedReciterId,
      selectedMoshafId,
      selectedChapterId,
      ayahFrom,
      ayahTo,
      includeBismillah,
      backgroundMode,
      backgroundIds,
      visualStyle: normalizeStyle(visualStyle),
      badgeEnabled,
      badgeArabicSurah,
      englishSurahTitle,
      arabicReciterTitle,
      englishReciterTitle
    });
  }, [
    activePage,
    arabicReciterTitle,
    ayahFrom,
    ayahTo,
    backgroundIds,
    backgroundMode,
    badgeArabicSurah,
    badgeEnabled,
    englishReciterTitle,
    englishSurahTitle,
    includeBismillah,
    reciterQuery,
    selectedChapterId,
    selectedMoshafId,
    selectedReciterId,
    styleLoaded,
    visualStyle
  ]);

  useEffect(() => {
    if (!styleLoaded) {
      return;
    }
    const timeout = window.setTimeout(() => {
      api.saveStyle(normalizeStyle(visualStyle)).catch((saveError: unknown) => setError(String(saveError)));
    }, 450);
    return () => window.clearTimeout(timeout);
  }, [styleLoaded, visualStyle]);

  useEffect(() => {
    if (!selectedReciterId || !selectedChapterId || (reciterRequiresMoshaf && !selectedMoshafId)) {
      setCompatibility(null);
      return;
    }
    api
      .compatibility(selectedReciterId, selectedChapterId, selectedMoshafId || undefined)
      .then(setCompatibility)
      .catch((compatibilityError: unknown) => setError(String(compatibilityError)));
  }, [reciterRequiresMoshaf, selectedMoshafId, selectedReciterId, selectedChapterId]);

  useEffect(() => {
    if (!selectedChapterId) {
      return;
    }
    api
      .verses(selectedChapterId)
      .then(setPreviewVerses)
      .catch((verseError: unknown) => setError(String(verseError)));
  }, [selectedChapterId]);

  useEffect(() => {
    if (!selectedChapter) {
      return;
    }
    const skipTitleSync = skipChapterTitleSyncRef.current;
    skipChapterTitleSyncRef.current = false;
    setAyahFrom((current) => clamp(current, 1, selectedChapter.verse_count));
    setAyahTo((current) => clamp(current, 1, selectedChapter.verse_count));
    if (!skipTitleSync) {
      setBadgeArabicSurah(selectedChapter.arabic_name);
      setEnglishSurahTitle(selectedChapter.english_name);
    }
  }, [selectedChapter]);

  useEffect(() => {
    if (!selectedChapter || !availableSurahSet || availableSurahSet.has(selectedChapter.id)) {
      return;
    }
    const nextChapterId = Array.from(availableSurahSet).sort((a, b) => a - b)[0];
    const nextChapter = chapters.find((chapter) => chapter.id === nextChapterId);
    if (nextChapter) {
      setSelectedChapterId(nextChapter.id);
      setAyahFrom(1);
      setAyahTo(nextChapter.verse_count);
    }
  }, [availableSurahSet, chapters, selectedChapter]);

  useEffect(() => {
    if (!selectedReciter?.moshafs?.length) {
      if (selectedMoshafId) {
        setSelectedMoshafId("");
      }
      return;
    }
    const selected = selectedReciter.moshafs.find((moshaf) => moshaf.id === selectedMoshafId);
    if (selected && selected.available_surahs.includes(selectedChapterId)) {
      return;
    }
    setSelectedMoshafId(defaultMoshafForReciter(selectedReciter, selectedChapterId));
  }, [selectedChapterId, selectedMoshafId, selectedReciter]);

  useEffect(() => {
    setAyahTo((current) => clamp(Math.max(current, ayahFrom), ayahFrom, verseCount));
  }, [ayahFrom, verseCount]);

  useEffect(() => {
    setAyahFromInput(String(ayahFrom));
  }, [ayahFrom]);

  useEffect(() => {
    setAyahToInput(String(ayahTo));
  }, [ayahTo]);

  useEffect(() => {
    if (!selectedReciter) {
      return;
    }
    const skipTitleSync = skipReciterTitleSyncRef.current;
    skipReciterTitleSyncRef.current = false;
    if (!skipTitleSync) {
      setBadgeArabicReciter(selectedReciter.arabic_name);
      setEnglishReciterTitle(selectedReciter.english_name);
    }
  }, [selectedReciter]);

  const showBismillahControl = selectedChapterId !== 1 && selectedChapterId !== 9 && ayahFrom === 1;

  useEffect(() => {
    setIncludeBismillah(showBismillahControl);
  }, [showBismillahControl]);

  const integerInputValue = (value: string, fallback: number) => {
    const trimmed = value.trim();
    if (!trimmed) {
      return fallback;
    }
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
  };

  const commitAyahFromInput = () => {
    const next = clamp(integerInputValue(ayahFromInput, ayahFrom), 1, verseCount);
    setAyahFrom(next);
    setAyahTo((current) => clamp(Math.max(current, next), next, verseCount));
    setAyahFromInput(String(next));
  };

  const commitAyahToInput = () => {
    const next = clamp(integerInputValue(ayahToInput, ayahTo), ayahFrom, verseCount);
    setAyahTo(next);
    setAyahToInput(String(next));
  };

  const renderBody = () => {
    const style = normalizeStyle(visualStyle);
    const renderBackgroundMode =
      backgroundMode === "slideshow" && backgroundIds.length >= 2 ? "slideshow" : "single";
    const renderBackgroundIds =
      renderBackgroundMode === "slideshow" ? backgroundIds : backgroundIds.slice(0, 1);
    return {
      reciter_id: selectedReciterId,
      moshaf_id: selectedMoshafId || null,
      chapter_id: selectedChapterId,
      ayah_from: ayahFrom,
      ayah_to: ayahTo,
      include_bismillah: includeBismillah,
      background_mode: renderBackgroundMode,
      background_ids: renderBackgroundIds,
      background_style: style.background_style,
      typography: style.typography,
      badge: {
        enabled: badgeEnabled,
        arabic_surah: artisticSurahName || badgeArabicSurah || selectedChapter?.arabic_name || "",
        english_surah: englishSurahTitle || selectedChapter?.english_name || "",
        arabic_reciter: arabicReciterTitle || selectedReciter?.arabic_name || "",
        english_reciter: englishReciterTitle || selectedReciter?.english_name || ""
      },
      badge_style: style.badge_style,
      thumbnail_style: style.thumbnail_style
    };
  };

  const compatibilityStatusText = compatibilityMessage(
    compatibility,
    reciterRequiresMoshaf,
    selectedMoshafId
  );
  const renderPrerequisiteIssue = !selectedChapter
    ? "Select a surah"
    : !selectedReciter
      ? "Select a reciter"
      : !backgroundIds.length
        ? "Select or upload a background"
        : reciterRequiresMoshaf && !selectedMoshaf
          ? "Select a moshaf / rewaya"
          : availableSurahSet && !availableSurahSet.has(selectedChapterId)
            ? "The selected moshaf does not include this surah."
            : ayahFrom < 1 || ayahFrom > ayahTo || ayahTo > verseCount
              ? "Fix the ayah range"
              : compatibility?.compatible
                ? null
                : compatibilityStatusText;
  const canRender = renderPrerequisiteIssue === null && Boolean(compatibility?.compatible);
  const renderInProgress = renderSubmitting || job?.status === "queued" || job?.status === "running";
  const renderDisabled = !canRender || renderInProgress;
  const renderDisabledReason = renderInProgress ? "A render is already running" : renderPrerequisiteIssue;

  const startRender = async () => {
    if (renderInProgress || renderSubmittingRef.current) {
      return;
    }
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    renderSubmittingRef.current = true;
    setRenderSubmitting(true);
    setError(null);
    try {
      const body = renderBody();
      await api.validateRender(body);
      const created = await api.createRender(body);
      setJob(created);
      if (["complete", "failed", "canceled"].includes(created.status)) {
        renderSubmittingRef.current = false;
        setRenderSubmitting(false);
        return;
      }
      const events = api.eventSource(created.job_id);
      eventSourceRef.current = events;
      events.onmessage = (event) => {
        const next = JSON.parse(event.data) as RenderJob;
        setJob(next);
        if (["complete", "failed", "canceled"].includes(next.status)) {
          renderSubmittingRef.current = false;
          setRenderSubmitting(false);
          events.close();
          eventSourceRef.current = null;
        }
      };
      events.onerror = () => {
        renderSubmittingRef.current = false;
        setRenderSubmitting(false);
        events.close();
        eventSourceRef.current = null;
      };
    } catch (renderError) {
      renderSubmittingRef.current = false;
      setRenderSubmitting(false);
      setError(String(renderError));
    }
  };

  const cancelCurrentRender = async () => {
    if (!job || !["queued", "running"].includes(job.status)) {
      return;
    }
    try {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      const canceled = await api.cancelRender(job.job_id);
      renderSubmittingRef.current = false;
      setRenderSubmitting(false);
      setJob(canceled);
    } catch (cancelError) {
      setError(String(cancelError));
    }
  };

  const uploadBackground = async (file: File | null) => {
    if (!file) {
      return;
    }
    const uploaded = await api.uploadBackground(file);
    const all = await api.backgrounds();
    setBackgrounds(all);
    setBackgroundIds([uploaded.id]);
    setBackgroundMode("single");
  };

  const controls = {
    setup: (
      <>
        <div className="control-band">
          <label htmlFor="reciter-search">Reciter</label>
          <div className="search-row">
            <Search size={17} />
            <input
              id="reciter-search"
              value={reciterQuery}
              onChange={(event) => setReciterQuery(event.target.value)}
              placeholder="Search reciter"
            />
          </div>
          <select
            aria-label="Reciter"
            value={selectedReciterId}
            onChange={(event) => {
              const nextReciter = reciters.find((reciter) => reciter.id === event.target.value);
              setSelectedReciterId(event.target.value);
              setSelectedMoshafId(defaultMoshafForReciter(nextReciter, selectedChapterId));
            }}
          >
            {visibleReciters.map((reciter) => (
              <option key={reciter.id} value={reciter.id}>
                {reciter.english_name} | {reciter.arabic_name} | {reciter.style.name}
              </option>
            ))}
          </select>
        </div>

        {selectedReciter?.moshafs?.length ? (
          <div className="control-band">
            <label htmlFor="moshaf-select">Moshaf / rewaya</label>
            <select
              id="moshaf-select"
              aria-label="Moshaf"
              value={selectedMoshafId}
              onChange={(event) => setSelectedMoshafId(event.target.value)}
            >
              {selectedReciter.moshafs.map((moshaf) => (
                <option key={moshaf.id} value={moshaf.id}>
                  {selectedReciter.english_name} — {moshaf.name} ({moshaf.available_surahs.length} surahs)
                </option>
              ))}
            </select>
          </div>
        ) : null}

        <div className="control-grid">
          <label>
            Surah
            <select
              aria-label="Surah"
              value={selectedChapterId}
              onChange={(event) => {
                const next = Number(event.target.value);
                const chapter = chapters.find((item) => item.id === next);
                setSelectedChapterId(next);
                setAyahFrom(1);
                setAyahTo(chapter?.verse_count ?? 1);
              }}
            >
              {chapters.map((chapter) => (
                <option
                  disabled={Boolean(availableSurahSet && !availableSurahSet.has(chapter.id))}
                  key={chapter.id}
                  value={chapter.id}
                >
                  {chapter.id}. {chapter.english_name} | {chapter.arabic_name} | 1-{chapter.verse_count}
                </option>
              ))}
            </select>
          </label>
          <label>
            First ayah
            <input
              aria-label="First ayah"
              type="number"
              value={ayahFromInput}
              onBlur={commitAyahFromInput}
              onChange={(event) => setAyahFromInput(event.target.value)}
            />
          </label>
          <label>
            Last ayah
            <input
              aria-label="Last ayah"
              type="number"
              value={ayahToInput}
              onBlur={commitAyahToInput}
              onChange={(event) => setAyahToInput(event.target.value)}
            />
          </label>
        </div>

        <div className="status-line">
          {compatibility?.compatible ? (
            <><CheckCircle2 size={18} /> {compatibilityStatusText}</>
          ) : (
            <><Ban size={18} /> {compatibilityStatusText}</>
          )}
        </div>
      </>
    ),
    background: (
      <div className="control-band">
        <div className="segmented">
          <button
            className={backgroundMode === "single" ? "selected" : ""}
            onClick={() => setBackgroundMode("single")}
            type="button"
          >
            <ImagePlus size={17} /> Single
          </button>
          <button
            className={backgroundMode === "slideshow" ? "selected" : ""}
            onClick={() => setBackgroundMode("slideshow")}
            type="button"
          >
            <ImagePlus size={17} /> Slideshow
          </button>
          <label className="upload-button">
            <Upload size={17} />
            <input
              aria-label="Upload background"
              type="file"
              accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.mkv,.webm"
              onChange={(event) => void uploadBackground(event.target.files?.[0] ?? null)}
            />
          </label>
        </div>
        <select
          multiple={backgroundMode === "slideshow"}
          aria-label="Background"
          value={backgroundMode === "single" ? (backgroundIds[0] ?? "") : backgroundIds}
          onChange={(event) => {
            const values = Array.from(event.currentTarget.selectedOptions).map((option) => option.value);
            setBackgroundIds(backgroundMode === "single" ? values.slice(0, 1) : values);
          }}
        >
          {backgrounds.map((background) => (
            <option key={background.id} value={background.id}>
              {background.filename} | {background.media_type}
            </option>
          ))}
        </select>
        <Range
          label="Background dim"
          value={effectiveStyle.background_style.dim_opacity}
          min={0}
          max={90}
          onChange={(value) => updateBackgroundStyle({ dim_opacity: value })}
        />
      </div>
    ),
    text: (
      <>
        <div className="control-grid">
          <Range
            label="Arabic size"
            value={effectiveStyle.typography.arabic_font_size}
            min={36}
            max={96}
            onChange={(value) => updateTypography({ arabic_font_size: value })}
          />
          <Range
            label="Translation size"
            value={effectiveStyle.typography.translation_font_size}
            min={22}
            max={58}
            onChange={(value) => updateTypography({ translation_font_size: value })}
          />
          <Range
            label="Arabic box X"
            value={effectiveStyle.typography.arabic_box_x}
            min={0}
            max={1920}
            onChange={(value) => updateTypography({ arabic_box_x: value })}
          />
          <Range
            label="Arabic box Y"
            value={effectiveStyle.typography.arabic_box_y}
            min={0}
            max={1080}
            onChange={(value) => updateTypography({ arabic_box_y: value, arabic_y: value })}
          />
          <Range
            label="Arabic box width"
            value={effectiveStyle.typography.arabic_box_width}
            min={120}
            max={1920}
            onChange={(value) => updateTypography({ arabic_box_width: value })}
          />
          <Range
            label="Arabic box height"
            value={effectiveStyle.typography.arabic_box_height}
            min={40}
            max={520}
            onChange={(value) => updateTypography({ arabic_box_height: value })}
          />
          <Range
            label="Translation box X"
            value={effectiveStyle.typography.translation_box_x}
            min={0}
            max={1920}
            onChange={(value) => updateTypography({ translation_box_x: value })}
          />
          <Range
            label="Translation box Y"
            value={effectiveStyle.typography.translation_box_y}
            min={0}
            max={1080}
            onChange={(value) => updateTypography({ translation_box_y: value, translation_y: value, gloss_y: value })}
          />
          <Range
            label="Translation box width"
            value={effectiveStyle.typography.translation_box_width}
            min={120}
            max={1920}
            onChange={(value) => updateTypography({ translation_box_width: value })}
          />
          <Range
            label="Translation box height"
            value={effectiveStyle.typography.translation_box_height}
            min={40}
            max={520}
            onChange={(value) => updateTypography({ translation_box_height: value })}
          />
          <Range
            label="Line spacing"
            value={effectiveStyle.typography.line_spacing}
            min={1}
            max={1.8}
            step={0.01}
            onChange={(value) => updateTypography({ line_spacing: value })}
          />
          <label>
            Text transition
            <select
              aria-label="Text transition"
              value={effectiveStyle.typography.text_transition}
              onChange={(event) =>
                updateTypography({
                  text_transition: event.target.value as VisualStyle["typography"]["text_transition"]
                })
              }
            >
              <option value="none">None</option>
              <option value="fade">Fade</option>
            </select>
          </label>
          {effectiveStyle.typography.text_transition === "fade" && (
            <Range
              label="Fade duration"
              value={effectiveStyle.typography.fade_duration_ms}
              min={50}
              max={2000}
              step={50}
              onChange={(value) => updateTypography({ fade_duration_ms: value })}
            />
          )}
          <label>
            Text layout
            <select
              aria-label="Text layout"
              value={effectiveStyle.typography.position}
              onChange={(event) => {
                const position = event.target.value as VisualStyle["typography"]["position"];
                updateTypography({ position, ...layoutPositions[position] });
              }}
            >
              <option value="top">Top</option>
              <option value="center">Center</option>
              <option value="bottom">Bottom</option>
            </select>
          </label>
        </div>
        <div className="font-grid" aria-label="Arabic font">
          {arabicFonts.map((font) => (
            <button
              className={font.key === effectiveStyle.typography.arabic_font_key ? "font-card selected" : "font-card"}
              key={font.key}
              type="button"
              onClick={() => updateTypography({ arabic_font_key: font.key })}
            >
              <span lang="ar" style={{ fontFamily: font.css }}>بسم</span>
              <strong>{font.label}</strong>
            </button>
          ))}
        </div>
        <div className="font-grid" aria-label="English font">
          {englishFonts.map((font) => (
            <button
              className={font.key === effectiveStyle.typography.english_font_key ? "font-card selected" : "font-card"}
              key={font.key}
              type="button"
              onClick={() => updateTypography({ english_font_key: font.key })}
            >
              <span style={{ fontFamily: font.css }}>Abc</span>
              <strong>{font.label}</strong>
            </button>
          ))}
        </div>
      </>
    ),
    badge: (
      <>
        <label className="toggle">
          <input
            type="checkbox"
            checked={badgeEnabled}
            onChange={(event) => setBadgeEnabled(event.target.checked)}
          />
          Permanent badge
        </label>
        <div className="control-grid">
          <Range label="Badge X" value={effectiveStyle.badge_style.x} max={1600} onChange={(value) => updateBadgeStyle({ x: value })} />
          <Range label="Badge Y" value={effectiveStyle.badge_style.y} max={360} onChange={(value) => updateBadgeStyle({ y: value })} />
          <Range
            label="Badge Arabic size"
            value={effectiveStyle.badge_style.artistic_surah_size}
            min={28}
            max={110}
            onChange={(value) => updateBadgeStyle({ artistic_surah_size: value })}
          />
          <Range
            label="Badge range size"
            value={effectiveStyle.badge_style.range_size}
            min={18}
            max={78}
            onChange={(value) => updateBadgeStyle({ range_size: value })}
          />
          <Range
            label="Badge gap"
            value={effectiveStyle.badge_style.line_gap}
            min={0}
            max={90}
            onChange={(value) => updateBadgeStyle({ line_gap: value })}
          />
        </div>
      </>
    ),
    thumbnail: (
      <>
        <div className="control-grid">
          <input aria-label="Arabic reciter title" value={arabicReciterTitle} onChange={(event) => setBadgeArabicReciter(event.target.value)} />
          <input aria-label="English reciter title" value={englishReciterTitle} onChange={(event) => setEnglishReciterTitle(event.target.value)} />
          <Range
            label="Thumbnail Arabic size"
            value={effectiveStyle.thumbnail_style.artistic_surah_size}
            min={12}
            max={220}
            onChange={(value) => updateThumbnailStyle({ artistic_surah_size: value })}
          />
          <Range
            label="Thumbnail Arabic Y"
            value={effectiveStyle.thumbnail_style.artistic_y}
            min={0}
            max={720}
            onChange={(value) => updateThumbnailStyle({ artistic_y: value })}
          />
          <Range
            label="Thumbnail English size"
            value={effectiveStyle.typography.translation_font_size}
            min={22}
            max={58}
            onChange={(value) => updateTypography({ translation_font_size: value })}
          />
          <Range
            label="Thumbnail English Y"
            value={effectiveStyle.thumbnail_style.english_y}
            min={0}
            max={720}
            onChange={(value) => updateThumbnailStyle({ english_y: value })}
          />
        </div>
      </>
    ),
    render: (
      <div className="render-panel">
        {showBismillahControl && (
          <label className="toggle">
            <input
              type="checkbox"
              checked={includeBismillah}
              onChange={(event) => setIncludeBismillah(event.target.checked)}
            />
            Include Bismillah introduction when source audio permits
          </label>
        )}
        <div className="status-line">
          Output: 1920x1080 MP4 and YouTube thumbnail
        </div>
        <button
          className="primary-action"
          type="button"
          disabled={renderDisabled}
          title={renderDisabledReason ?? "Start render"}
          onClick={() => void startRender()}
        >
          {renderInProgress ? <LoaderCircle size={18} className="spin" /> : <Play size={18} />}
          {renderInProgress ? "Rendering..." : "Start render"}
        </button>
        {renderDisabledReason && !renderInProgress && (
          <div className="status-line">
            <Ban size={18} /> {renderDisabledReason}
          </div>
        )}
        {job && (
          <>
            <div className="progress-row">
              <LoaderCircle size={18} className={job.status === "running" ? "spin" : ""} />
              <span>{job.phase}</span>
              <strong>{Math.round(job.progress)}%</strong>
            </div>
            <progress value={job.progress} max={100} />
            {job.error_summary && <div className="alert">{job.error_summary}</div>}
            {["queued", "running"].includes(job.status) && (
              <button type="button" onClick={() => void cancelCurrentRender()}>
                <Square size={18} /> Cancel
              </button>
            )}
            {job.status === "complete" && (
              <div className="downloads">
                <a href={api.videoUrl(job.job_id)}><Download size={17} /> MP4</a>
                <a href={api.thumbnailUrl(job.job_id)}><Download size={17} /> Thumbnail</a>
                <a href={api.outputsUrl(job.job_id)} target="_blank" rel="noreferrer">
                  <FolderOpen size={17} /> Show outputs
                </a>
              </div>
            )}
          </>
        )}
      </div>
    )
  } satisfies Record<PageId, ReactNode>;

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1>Quran Video Editor</h1>
          <p>{englishSurahTitle || "Surah"} • {ayahFrom}-{ayahTo}</p>
        </div>
        <div className="doctor">
          <CheckCircle2 size={18} />
          <span>Arabic + translation</span>
        </div>
      </header>

      {error && <div className="alert">{error}</div>}

      <section className="workspace">
        <aside className="steps" aria-label="Editor pages">
          {pages.map((page, index) => (
            <button
              className={page.id === activePage ? "page-tab selected" : "page-tab"}
              key={page.id}
              type="button"
              onClick={() => setActivePage(page.id)}
            >
              <span>{index + 1}</span>
              <strong>{page.label}</strong>
              <small>{page.detail}</small>
            </button>
          ))}
        </aside>

        <section className="controls">
          <div className="page-head">
            <div>
              <h2>{pages[pageIndex]?.label}</h2>
              <p>{pages[pageIndex]?.detail}</p>
            </div>
            <div className="page-nav">
              <button type="button" disabled={pageIndex <= 0} onClick={() => setActivePage(pages[pageIndex - 1].id)}>
                Back
              </button>
              <button type="button" disabled={pageIndex >= pages.length - 1} onClick={() => setActivePage(pages[pageIndex + 1].id)}>
                Next
              </button>
            </div>
          </div>
          {controls[activePage]}
        </section>

        <section className="preview-pane">
          <div className="preview" aria-label="Composition preview" ref={compositionRef}>
            <div
              className="render-stage"
              style={{
                width: 1920,
                height: 1080,
                transform: `scale(${compositionScale})`
              }}
            >
              {selectedBackground?.media_type === "video" ? (
                <video className="preview-media" src={selectedBackgroundUrl} muted loop playsInline autoPlay />
              ) : selectedBackground ? (
                <img className="preview-media" src={selectedBackgroundUrl} alt="" />
              ) : (
                <div className="preview-bg" />
              )}
              <div
                className="background-dim"
                style={{ opacity: effectiveStyle.background_style.dim_opacity / 100 }}
              />
              {badgeEnabled && (
                <div
                  className="badge badge-row"
                  style={{
                    gap: px(effectiveStyle.badge_style.line_gap),
                    left: px(effectiveStyle.badge_style.x),
                    top: px(effectiveStyle.badge_style.y)
                  }}
                >
                  <strong
                    className="badge-art"
                    lang="ar"
                    style={{
                      fontFamily: badgeArabicFontCss,
                      fontSize: px(effectiveStyle.badge_style.artistic_surah_size)
                    }}
                  >
                    {badgeSurahGlyph(selectedChapterId)}
                  </strong>
                  <span
                    className="badge-meta"
                    style={{
                      fontSize: px(effectiveStyle.badge_style.range_size),
                      marginTop: px(Math.round(effectiveStyle.badge_style.artistic_surah_size * 0.16))
                    }}
                  >
                    {ayahFrom}-{ayahTo}
                  </span>
                </div>
              )}
              <FitBoxText
                text={previewArabicText}
                className="preview-arabic"
                direction="rtl"
                verticalAlign="bottom"
                fontFamily={arabicFontFamily}
                baseFontSize={effectiveStyle.typography.arabic_font_size}
                lineHeight={effectiveStyle.typography.line_spacing}
                x={effectiveStyle.typography.arabic_box_x}
                y={effectiveStyle.typography.arabic_box_y}
                width={effectiveStyle.typography.arabic_box_width}
                height={effectiveStyle.typography.arabic_box_height}
                transitionMode={effectiveStyle.typography.text_transition}
                fadeDurationMs={effectiveStyle.typography.fade_duration_ms}
                ayahMarkerNumber={previewArabicAyahNumber}
              />
              <FitBoxText
                text={previewTranslationText}
                className="preview-translation"
                verticalAlign="top"
                fontFamily={englishFontFamily}
                baseFontSize={effectiveStyle.typography.translation_font_size}
                lineHeight={effectiveStyle.typography.line_spacing}
                x={effectiveStyle.typography.translation_box_x}
                y={effectiveStyle.typography.translation_box_y}
                width={effectiveStyle.typography.translation_box_width}
                height={effectiveStyle.typography.translation_box_height}
                transitionMode={effectiveStyle.typography.text_transition}
                fadeDurationMs={effectiveStyle.typography.fade_duration_ms}
                italic
              />
            </div>
          </div>

          <div className="thumbnail-preview" aria-label="Thumbnail preview" ref={thumbnailRef}>
            <div
              className="render-stage"
              style={{
                width: 1280,
                height: 720,
                transform: `scale(${thumbnailScale})`
              }}
            >
              {selectedBackground?.media_type === "video" ? (
                <video className="preview-media" src={selectedBackgroundUrl} muted loop playsInline autoPlay />
              ) : selectedBackground ? (
                <img className="preview-media" src={selectedBackgroundUrl} alt="" />
              ) : (
                <div className="preview-bg" />
              )}
              <div
                className="background-dim"
                style={{ opacity: effectiveStyle.background_style.dim_opacity / 100 }}
              />
              <strong
                lang="ar"
                style={{
                  top: px(effectiveStyle.thumbnail_style.artistic_y),
                  fontFamily: arabicFontFamily,
                  fontSize: px(effectiveStyle.thumbnail_style.artistic_surah_size)
                }}
              >
                سورة {artisticSurahName} | {arabicReciterTitle}
              </strong>
              <span
                style={{
                  top: px(effectiveStyle.thumbnail_style.english_y),
                  fontFamily: englishFontFamily,
                  fontSize: px(effectiveStyle.typography.translation_font_size)
                }}
              >
                Surah {englishSurahTitle} | {englishReciterTitle}
              </span>
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}

function Range({
  label,
  value,
  min = 0,
  max = 255,
  step = 1,
  onChange
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span className="range-label">
        {label}
        <span className="range-value" aria-hidden="true">
          {Number.isInteger(value) ? value : value.toFixed(2)}
        </span>
      </span>
      <input
        aria-label={label}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
