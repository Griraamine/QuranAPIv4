export interface Chapter {
  id: number;
  arabic_name: string;
  artistic_arabic_name?: string;
  english_name: string;
  translated_name: string | null;
  verse_count: number;
  revelation_place: string | null;
}

export interface Moshaf {
  id: string;
  name: string;
  rewaya_id: number | null;
  rewaya: string | null;
  moshaf_type: number | null;
  server: string;
  surah_total: number;
  available_surahs: number[];
  timing_status: string | null;
  timing_read_id: number | null;
  timing_mapping_method: string | null;
}

export interface Reciter {
  id: string;
  english_name: string;
  arabic_name: string;
  style: { id: string; name: string };
  audio_source_name?: string;
  provider?: string;
  moshafs?: Moshaf[];
}

export interface BackgroundAsset {
  id: string;
  filename: string;
  media_type: "image" | "video";
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
}

export interface Compatibility {
  reciter_id: string;
  moshaf_id: string | null;
  chapter_id: number;
  compatible: boolean;
  reason: string | null;
  has_word_timing: boolean;
  has_ayah_timing: boolean;
  status: string | null;
  timing_read_id: number | null;
  timing_mapping_method: string | null;
}

export interface VersePreview {
  chapter_id: number;
  verse_number: number;
  text_uthmani: string;
  translation: string;
}

export interface RenderJob {
  job_id: string;
  status: "queued" | "running" | "complete" | "failed" | "canceled";
  phase: string;
  progress: number;
  eta_seconds: number | null;
  error_summary: string | null;
  video_path: string | null;
  thumbnail_path: string | null;
}

export interface TypographyStyle {
  arabic_font_size: number;
  gloss_font_size: number;
  translation_font_size: number;
  text_shade: string;
  secondary_shade: string;
  outline_px: number;
  shadow_px: number;
  line_spacing: number;
  position: "top" | "center" | "bottom";
  arabic_y: number;
  gloss_y: number;
  translation_y: number;
  arabic_box_x: number;
  arabic_box_y: number;
  arabic_box_width: number;
  arabic_box_height: number;
  translation_box_x: number;
  translation_box_y: number;
  translation_box_width: number;
  translation_box_height: number;
  text_transition: "none" | "fade";
  fade_duration_ms: number;
  arabic_font_key:
    | "uthmanic"
    | "amiri"
    | "noto_naskh"
    | "scheherazade"
    | "scheherazade_b"
    | "lateef"
    | "indo_pak"
    | "al_mushaf"
    | "poetry"
    | "hafs_ex1"
    | "muhammadi"
    | "me_quran"
    | "nabi"
    | "aref_ruqaa"
    | "mirza"
    | "reem_kufi"
    | "harmattan"
    | "system";
  english_font_key: "system" | "georgia" | "palatino" | "times" | "avenir" | "didot";
}

export interface BadgeStyle {
  x: number;
  y: number;
  artistic_surah_size: number;
  english_size: number;
  range_size: number;
  line_gap: number;
  shade: string;
  secondary_shade: string;
  show_reciter: boolean;
}

export interface ThumbnailStyle {
  artistic_surah_size: number;
  artistic_y: number;
  artistic_shade: string;
  show_english: boolean;
  english_size: number;
  english_y: number;
  english_shade: string;
  shadow_px: number;
}

export interface BackgroundStyle {
  dim_opacity: number;
}

export interface VisualStyle {
  background_style: BackgroundStyle;
  typography: TypographyStyle;
  badge_style: BadgeStyle;
  thumbnail_style: ThumbnailStyle;
}
