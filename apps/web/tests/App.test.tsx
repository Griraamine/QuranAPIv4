import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";

const chapters = [
  {
    id: 1,
    arabic_name: "الفاتحة",
    artistic_arabic_name: "الْفَاتِحَة",
    english_name: "Al-Fatihah",
    translated_name: "The Opener",
    verse_count: 7,
    revelation_place: "makkah"
  }
];

const reciters = [
  {
    id: "fixture-reciter",
    english_name: "Fixture Reciter",
    arabic_name: "القارئ التجريبي",
    style: { id: "murattal", name: "Murattal" }
  },
  {
    id: "fixture-incompatible",
    english_name: "Incomplete Fixture",
    arabic_name: "غير مكتمل",
    style: { id: "murattal", name: "Murattal" }
  },
  {
    id: "fixture-timing-unavailable",
    english_name: "No Timing Fixture",
    arabic_name: "بلا توقيت",
    style: { id: "murattal", name: "Murattal" }
  },
  {
    id: "fixture-multi-moshaf",
    english_name: "Multi Moshaf Fixture",
    arabic_name: "متعدد المصاحف",
    style: { id: "mp3quran", name: "MP3Quran" },
    moshafs: [
      {
        id: "mojawwad",
        name: "Mojawwad",
        rewaya_id: null,
        rewaya: null,
        moshaf_type: null,
        server: "https://server.example.test/mojawwad/",
        surah_total: 1,
        available_surahs: [1],
        timing_status: "timing_available",
        timing_read_id: 2,
        timing_mapping_method: "server"
      },
      {
        id: "murattal",
        name: "Murattal",
        rewaya_id: null,
        rewaya: null,
        moshaf_type: null,
        server: "https://server.example.test/murattal/",
        surah_total: 1,
        available_surahs: [1],
        timing_status: "timing_available",
        timing_read_id: 1,
        timing_mapping_method: "server"
      }
    ]
  }
];

const backgrounds = [
  {
    id: "sample.jpg",
    filename: "sample.jpg",
    media_type: "image",
    width: 1920,
    height: 1080,
    duration_seconds: null
  }
];

const verses = [
  {
    chapter_id: 1,
    verse_number: 1,
    text_uthmani: "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
    translation: "In the name of Allah, the Entirely Merciful, the Especially Merciful."
  },
  {
    chapter_id: 1,
    verse_number: 2,
    text_uthmani: "ٱلْحَمْدُ لِلَّهِ رَبِّ ٱلْعَـٰلَمِينَ",
    translation: "All praise is due to Allah, Lord of the worlds."
  }
];

const style = {
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
    text_transition: "none",
    fade_duration_ms: 350,
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
    artistic_surah_size: 112,
    artistic_y: 350,
    artistic_shade: "#FFFFFF",
    show_english: true,
    english_size: 62,
    english_y: 402,
    english_shade: "#FFFFFF",
    shadow_px: 0
  }
};

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/v1/chapters")) {
      return Response.json(chapters);
    }
    if (url.endsWith("/api/v1/chapters/1/verses")) {
      return Response.json(verses);
    }
    if (url.endsWith("/api/v1/reciters")) {
      return Response.json(reciters);
    }
    if (url.endsWith("/api/v1/backgrounds")) {
      return Response.json(backgrounds);
    }
    if (url.endsWith("/api/v1/style")) {
      return Response.json(init?.method === "PUT" && init.body ? JSON.parse(String(init.body)) : style);
    }
    if (url.includes("/api/v1/compatibility")) {
      const incompatible = url.includes("fixture-incompatible");
      const timingUnavailable = url.includes("fixture-timing-unavailable");
      return Response.json({
        reciter_id: incompatible
          ? "fixture-incompatible"
          : timingUnavailable
            ? "fixture-timing-unavailable"
            : "fixture-reciter",
        chapter_id: 1,
        compatible: !incompatible && !timingUnavailable,
        reason: incompatible
          ? "fixture reciter intentionally lacks complete word timing"
          : timingUnavailable
            ? "Quran.Foundation audio response has no ayah timestamps"
            : null,
        status: timingUnavailable ? "timing_unavailable" : null,
        has_word_timing: !incompatible && !timingUnavailable
      });
    }
    if (url.endsWith("/api/v1/render/validate")) {
      return Response.json({ compatible: true, reason: null });
    }
    if (url.endsWith("/api/v1/renders") && init?.method === "POST") {
      return Response.json({
        job_id: "job-1",
        status: "running",
        phase: "encoding",
        progress: 55,
        eta_seconds: 10,
        error_summary: null,
        video_path: null,
        thumbnail_path: null
      });
    }
    if (url.endsWith("/cancel")) {
      return Response.json({
        job_id: "job-1",
        status: "canceled",
        phase: "canceled",
        progress: 0,
        eta_seconds: null,
        error_summary: null,
        video_path: null,
        thumbnail_path: null
      });
    }
    return new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Quran video editor", () => {
  it("shows structured API error messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/chapters")) {
          return Response.json(
            {
              detail: {
                code: "quran_foundation_configuration",
                message: "Set QF_CLIENT_ID and QF_CLIENT_SECRET, or use fixture mode."
              }
            },
            { status: 503 }
          );
        }
        return Response.json([]);
      })
    );

    render(<App />);

    expect(
      await screen.findByText("Error: Set QF_CLIENT_ID and QF_CLIENT_SECRET, or use fixture mode.")
    ).toBeInTheDocument();
  });

  it("restores editor controls from the saved cookie", async () => {
    document.cookie = `quran_video_editor_state=${encodeURIComponent(JSON.stringify({
      activePage: "text",
      reciterQuery: "fixture",
      selectedReciterId: "fixture-reciter",
      selectedChapterId: 1,
      ayahFrom: 2,
      ayahTo: 4,
      includeBismillah: false,
      backgroundMode: "single",
      backgroundIds: ["sample.jpg"],
      visualStyle: {
        ...style,
        typography: {
          ...style.typography,
          arabic_font_size: 82,
          english_font_key: "georgia"
        }
      },
      badgeEnabled: true,
      badgeArabicSurah: "Saved Arabic",
      englishSurahTitle: "Saved English",
      arabicReciterTitle: "Saved Reciter Arabic",
      englishReciterTitle: "Saved Reciter"
    }))}; Path=/`;
    installFetchMock();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Text" })).toBeInTheDocument();
    expect(screen.getByText("Saved English • 2-4")).toBeInTheDocument();
    expect(screen.getByLabelText("Arabic size")).toHaveValue("82");
    expect(screen.getByRole("button", { name: /georgia/i })).toHaveClass("selected");

    fireEvent.click(screen.getByText("Setup"));
    expect(await screen.findByLabelText("First ayah")).toHaveValue(2);
    expect(screen.getByLabelText("Last ayah")).toHaveValue(4);
    expect(screen.getByRole("combobox", { name: "Reciter" })).toHaveValue("fixture-reciter");
  });

  it("loads grouped pages and clamps ayah ranges to the selected surah", async () => {
    installFetchMock();
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Setup" })).toBeInTheDocument();
    expect(screen.getByLabelText("Composition preview")).toBeInTheDocument();
    expect(screen.getByLabelText("Ayah 1")).toBeInTheDocument();
    const firstAyah = await screen.findByLabelText("First ayah");
    expect(screen.getByLabelText("Last ayah")).toHaveValue(7);
    fireEvent.change(firstAyah, { target: { value: "" } });
    expect(firstAyah).toHaveDisplayValue("");
    fireEvent.change(firstAyah, { target: { value: "99" } });
    expect(firstAyah).toHaveValue(99);
    fireEvent.blur(firstAyah);
    const lastAyah = screen.getByLabelText("Last ayah");
    await waitFor(() => expect(firstAyah).toHaveValue(7));
    expect(lastAyah).toHaveValue(7);
  });

  it("shows incompatibility messages and disables render", async () => {
    installFetchMock();
    render(<App />);
    const reciter = await screen.findByRole("combobox", { name: "Reciter" });
    fireEvent.change(reciter, { target: { value: "fixture-incompatible" } });
    expect(await screen.findByText("fixture reciter intentionally lacks complete word timing")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Render"));
    expect(screen.getByRole("button", { name: /start render/i })).toBeDisabled();
  });

  it("explains timing-unavailable selections instead of only disabling render", async () => {
    installFetchMock();
    render(<App />);
    const reciter = await screen.findByRole("combobox", { name: "Reciter" });
    fireEvent.change(reciter, { target: { value: "fixture-timing-unavailable" } });

    expect(await screen.findByText("Quran.Foundation audio response has no ayah timestamps")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Render"));
    const button = screen.getByRole("button", { name: /start render/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      "title",
      expect.stringContaining("no ayah timestamps")
    );
  });

  it("defaults multi-moshaf reciters to Murattal", async () => {
    installFetchMock();
    render(<App />);
    const reciter = await screen.findByRole("combobox", { name: "Reciter" });
    fireEvent.change(reciter, { target: { value: "fixture-multi-moshaf" } });

    expect(await screen.findByRole("combobox", { name: "Moshaf" })).toHaveValue("murattal");
  });

  it("removes color controls, edits thumbnail reciter text, and changes Arabic font", async () => {
    installFetchMock();
    render(<App />);
    expect(screen.queryByLabelText("Primary shade")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("Text"));
    expect(screen.getByLabelText("Arabic box width")).toBeInTheDocument();
    expect(screen.getByLabelText("Translation box height")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /reem kufi/i }));
    expect(screen.getByRole("button", { name: /reem kufi/i })).toHaveClass("selected");
    fireEvent.click(screen.getByRole("button", { name: /georgia/i }));
    expect(screen.getByRole("button", { name: /georgia/i })).toHaveClass("selected");
    fireEvent.click(screen.getByText("Thumbnail"));
    fireEvent.change(screen.getByLabelText("English reciter title"), { target: { value: "Edited Reciter" } });
    expect(await screen.findByText(/Edited Reciter/)).toBeInTheDocument();
  });

  it("keeps the badge Arabic-only with ayah range", async () => {
    installFetchMock();
    render(<App />);
    expect(await screen.findByText("\uE001")).toBeInTheDocument();
    expect(screen.getByText("1-7")).toBeInTheDocument();
    expect(screen.queryByLabelText("English surah badge")).not.toBeInTheDocument();
  });

  it("submits render, handles SSE progress, supports cancellation and download links", async () => {
    const fetchMock = installFetchMock();
    render(<App />);
    fireEvent.click(await screen.findByText("Render"));
    const button = await screen.findByRole("button", { name: /start render/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);
    fireEvent.click(button);
    expect(await screen.findByRole("button", { name: /rendering/i })).toBeDisabled();
    expect(await screen.findByText("encoding")).toBeInTheDocument();
    const createCalls = fetchMock.mock.calls.filter(
      ([url, init]) => String(url).endsWith("/api/v1/renders") && init?.method === "POST"
    );
    expect(createCalls).toHaveLength(1);
    const eventSource = (globalThis as any).MockEventSource.instances.at(-1);
    eventSource.emit({
      job_id: "job-1",
      status: "complete",
      phase: "complete",
      progress: 100,
      eta_seconds: null,
      error_summary: null,
      video_path: "/tmp/quran-video/job-1/video.mp4",
      thumbnail_path: "/tmp/quran-video/job-1/thumbnail.jpg"
    });
    expect(await screen.findByText("MP4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /thumbnail/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /show outputs/i })).toHaveAttribute(
      "href",
      "/api/v1/renders/job-1/outputs"
    );
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/render/validate"), expect.anything());
  });

  it("cancels a running render and updates the render page state", async () => {
    const fetchMock = installFetchMock();
    render(<App />);
    fireEvent.click(await screen.findByText("Render"));
    const button = await screen.findByRole("button", { name: /start render/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);
    expect(await screen.findByText("encoding")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(await screen.findByText("canceled")).toBeInTheDocument();
    const cancelCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/cancel"));
    expect(cancelCalls).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
  });

  it("saves background dim and normalizes one-background slideshow renders to single mode", async () => {
    const fetchMock = installFetchMock();
    render(<App />);
    fireEvent.click(await screen.findByText("Background"));
    fireEvent.click(screen.getByRole("button", { name: /slideshow/i }));
    fireEvent.change(screen.getByLabelText("Background dim"), { target: { value: "55" } });
    fireEvent.click(screen.getByText("Render"));
    const button = await screen.findByRole("button", { name: /start render/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/render/validate"), expect.anything()));
    const validateCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/v1/render/validate"));
    const body = JSON.parse(String(validateCall?.[1]?.body));
    expect(body.background_mode).toBe("single");
    expect(body.background_ids).toEqual(["sample.jpg"]);
    expect(body.background_style.dim_opacity).toBe(55);
    expect(body).not.toHaveProperty("data_mode");
  });

  it("sends selected text transition settings with render requests", async () => {
    const fetchMock = installFetchMock();
    render(<App />);
    fireEvent.click(await screen.findByText("Text"));
    fireEvent.change(screen.getByLabelText("Text transition"), { target: { value: "fade" } });
    fireEvent.change(await screen.findByLabelText("Fade duration"), { target: { value: "650" } });
    expect(document.querySelector(".preview-fit-text-fade")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Render"));
    const button = await screen.findByRole("button", { name: /start render/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/render/validate"), expect.anything())
    );
    const validateCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/v1/render/validate"));
    const body = JSON.parse(String(validateCall?.[1]?.body));
    expect(body.typography.text_transition).toBe("fade");
    expect(body.typography.fade_duration_ms).toBe(650);
  });
});
