import { expect, test } from "@playwright/test";

test("editor renders with mocked API state", async ({ page }) => {
  await page.route("**/api/v1/chapters", (route) =>
    route.fulfill({
      json: [
        {
          id: 1,
          arabic_name: "الفاتحة",
          artistic_arabic_name: "الْفَاتِحَة",
          english_name: "Al-Fatihah",
          translated_name: "The Opener",
          verse_count: 7,
          revelation_place: "makkah"
        }
      ]
    })
  );
  await page.route("**/api/v1/chapters/1/verses", (route) =>
    route.fulfill({
      json: [
        {
          chapter_id: 1,
          verse_number: 1,
          text_uthmani: "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
          translation: "In the name of Allah, the Entirely Merciful, the Especially Merciful."
        }
      ]
    })
  );
  await page.route("**/api/v1/reciters", (route) =>
    route.fulfill({
      json: [
        {
          id: "fixture-reciter",
          english_name: "Fixture Reciter",
          arabic_name: "القارئ التجريبي",
          style: { id: "murattal", name: "Murattal" }
        }
      ]
    })
  );
  await page.route("**/api/v1/backgrounds", (route) =>
    route.fulfill({
      json: [
        {
          id: "sample.jpg",
          filename: "sample.jpg",
          media_type: "image",
          width: 1920,
          height: 1080,
          duration_seconds: null
        }
      ]
    })
  );
  await page.route("**/api/v1/style", (route) =>
    route.fulfill({
      json: {
        background_style: {
          dim_opacity: 35
        },
        typography: {
          arabic_font_size: 70,
          gloss_font_size: 34,
          translation_font_size: 42,
          text_shade: "#FFFFFF",
          secondary_shade: "#D9D9D9",
          outline_px: 3,
          shadow_px: 5,
          line_spacing: 1.22,
          position: "center",
          arabic_y: 470,
          gloss_y: 610,
          translation_y: 685,
          arabic_font_key: "amiri",
          english_font_key: "times"
        },
        badge_style: {
          x: 128,
          y: 88,
          artistic_surah_size: 58,
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
      }
    })
  );
  await page.route("**/api/v1/compatibility**", (route) =>
    route.fulfill({
      json: {
        reciter_id: "fixture-reciter",
        chapter_id: 1,
        compatible: true,
        reason: null,
        has_word_timing: true
      }
    })
  );
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Quran Video Editor" })).toBeVisible();
  await expect(page.getByLabel("Composition preview")).toBeVisible();
  await expect(page.getByRole("button", { name: /Text Font and layout/ })).toBeVisible();
});
