import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Check, CloudUpload } from "lucide-react";
import { cn } from "@/lib/utils";

const MAX_FILE_BYTES = 50 * 1024 * 1024;
const NARRATION_PRESETS = [
  {
    id: "elevenlabs_matilda_flash",
    avatar: "11",
    name: "ElevenLabs Matilda",
    subtitle: "Eleven Flash v2.5",
    width: "42%",
    length: "Natural",
    provider: "elevenlabs",
    model: "eleven_flash_v2_5",
    voice: "Matilda",
  },
  {
    id: "elevenlabs_matilda_v3",
    avatar: "11",
    name: "ElevenLabs Matilda",
    subtitle: "Eleven v3",
    width: "36%",
    length: "Expressive",
    provider: "elevenlabs",
    model: "eleven_v3",
    voice: "Matilda",
  },
  {
    id: "openai_gpt_mini",
    avatar: "OA",
    name: "OpenAI GPT Mini",
    subtitle: "gpt-4o-mini-tts",
    width: "30%",
    length: "Legacy",
    provider: "openai",
    model: "gpt-4o-mini-tts",
    voice: "marin",
  },
];
const DEFAULT_NARRATION_PRESET = NARRATION_PRESETS[0].id;

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function resolveBackendOrigin() {
  const configured = trimTrailingSlash(process.env.NEXT_PUBLIC_BACKEND_ORIGIN || "");
  if (configured) {
    return configured;
  }
  if (typeof window === "undefined") {
    return "";
  }
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") {
    return `${window.location.protocol}//${host}:8100`;
  }
  return "";
}

function formatFileSize(bytes) {
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

function validatePdf(file) {
  if (!file) {
    return { ok: false, message: "Choose a PDF file to continue." };
  }
  const name = (file.name || "").toLowerCase();
  const isPdf = file.type === "application/pdf" || name.endsWith(".pdf");
  if (!isPdf) {
    return { ok: false, message: "Only PDF upload is supported right now." };
  }
  if (file.size > MAX_FILE_BYTES) {
    return { ok: false, message: "File is larger than 50MB." };
  }
  return { ok: true, message: `Selected: ${file.name} (${formatFileSize(file.size)})` };
}

function validateLessonFiles(files) {
  if (!Array.isArray(files) || files.length < 2) {
    return { ok: false, message: "Choose at least 2 PDF decks for Lesson Mode." };
  }
  for (const file of files) {
    const validation = validatePdf(file);
    if (!validation.ok) {
      return validation;
    }
  }
  const totalMb = files.reduce((sum, file) => sum + file.size, 0) / (1024 * 1024);
  return {
    ok: true,
    message: `${files.length} decks selected (${totalMb.toFixed(1)} MB total).`,
  };
}

export default function NewProjectPage() {
  const router = useRouter();
  const lectureInputRef = useRef(null);
  const lessonInputRef = useRef(null);
  const [mode, setMode] = useState("lecture");
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedLessonFiles, setSelectedLessonFiles] = useState([]);
  const [lectureFileStatus, setLectureFileStatus] = useState("No file selected.");
  const [lessonFileStatus, setLessonFileStatus] = useState("No files selected.");
  const [submitStatus, setSubmitStatus] = useState("");
  const [submitError, setSubmitError] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [selectedNarrationPreset, setSelectedNarrationPreset] = useState(DEFAULT_NARRATION_PRESET);
  const [useLessonMockMode, setUseLessonMockMode] = useState(true);

  const lectureValidation = validatePdf(selectedFile);
  const lessonValidation = validateLessonFiles(selectedLessonFiles);
  const activeValidation = mode === "lecture" ? lectureValidation : lessonValidation;
  const activeFileStatus = mode === "lecture" ? lectureFileStatus : lessonFileStatus;
  const canSubmit = activeValidation.ok && !isSubmitting;

  const applyLectureFile = (file, source = "picker") => {
    const validation = validatePdf(file);
    if (!validation.ok) {
      setSelectedFile(null);
      setLectureFileStatus(validation.message);
      setSubmitStatus("");
      setSubmitError(true);
      return;
    }
    setSelectedFile(file);
    setLectureFileStatus(validation.message);
    setSubmitStatus(source === "drop" ? "File added from drop area." : "");
    setSubmitError(false);
  };

  const applyLessonFiles = (files, source = "picker") => {
    const nextFiles = Array.from(files || []);
    const validation = validateLessonFiles(nextFiles);
    if (!validation.ok) {
      setSelectedLessonFiles([]);
      setLessonFileStatus(validation.message);
      setSubmitStatus("");
      setSubmitError(true);
      return;
    }
    setSelectedLessonFiles(nextFiles);
    setLessonFileStatus(validation.message);
    setSubmitStatus(source === "drop" ? "Decks added from drop area." : "");
    setSubmitError(false);
  };

  const submitLecture = async () => {
    const validation = validatePdf(selectedFile);
    if (!validation.ok) {
      setLectureFileStatus(validation.message);
      setSubmitError(true);
      return;
    }

    const backendOrigin = resolveBackendOrigin();
    const narrationPreset = NARRATION_PRESETS.find((item) => item.id === selectedNarrationPreset) || NARRATION_PRESETS[0];
    const query = new URLSearchParams({
      method: "pelt",
      use_cache: "true",
      skip_generation: "false",
      tts_provider: narrationPreset.provider,
      tts_model: narrationPreset.model,
      tts_voice: narrationPreset.voice,
    });
    const processUrl = `${backendOrigin}/api/process-pdf?${query.toString()}`;

    setIsSubmitting(true);
    setSubmitError(false);
    setSubmitStatus(`Submitting PDF to pipeline with ${narrationPreset.name}...`);

    const formData = new FormData();
    formData.append("pdf", selectedFile);

    try {
      const response = await fetch(processUrl, {
        method: "POST",
        body: formData,
      });
      const rawBody = await response.text();
      let payload = null;
      try {
        payload = rawBody ? JSON.parse(rawBody) : null;
      } catch {
        payload = null;
      }

      if (!response.ok) {
        const message = payload?.detail || (rawBody || "").trim() || `Request failed with status ${response.status}.`;
        throw new Error(message);
      }

      setSubmitStatus("Lecture generated successfully. Redirecting to dashboard...");
      setTimeout(() => {
        void router.push("/");
      }, 700);
    } catch (error) {
      setSubmitStatus(error?.message || "Failed to generate lecture.");
      setSubmitError(true);
      setIsSubmitting(false);
    }
  };

  const submitLessons = async () => {
    const validation = validateLessonFiles(selectedLessonFiles);
    if (!validation.ok) {
      setLessonFileStatus(validation.message);
      setSubmitError(true);
      return;
    }

    const backendOrigin = resolveBackendOrigin();
    const query = new URLSearchParams({
      skip_generation: "false",
      mock: useLessonMockMode ? "true" : "false",
    });
    const processUrl = `${backendOrigin}/api/process-lessons?${query.toString()}`;

    setIsSubmitting(true);
    setSubmitError(false);
    setSubmitStatus(
      useLessonMockMode
        ? `Building a local test course from ${selectedLessonFiles.length} decks...`
        : `Submitting ${selectedLessonFiles.length} decks for lesson clustering...`
    );

    const formData = new FormData();
    for (const file of selectedLessonFiles) {
      formData.append("pdfs", file);
    }

    try {
      const response = await fetch(processUrl, {
        method: "POST",
        body: formData,
      });
      const rawBody = await response.text();
      let payload = null;
      try {
        payload = rawBody ? JSON.parse(rawBody) : null;
      } catch {
        payload = null;
      }

      if (!response.ok) {
        const message = payload?.detail || (rawBody || "").trim() || `Request failed with status ${response.status}.`;
        throw new Error(message);
      }

      const jobId = payload?.job_id;
      if (!jobId) {
        throw new Error("Lesson job finished without a job ID.");
      }
      setSubmitStatus("Lessons generated successfully. Redirecting to course view...");
      setTimeout(() => {
        void router.push(`/course/${encodeURIComponent(jobId)}`);
      }, 500);
    } catch (error) {
      setSubmitStatus(error?.message || "Failed to generate lesson buckets.");
      setSubmitError(true);
      setIsSubmitting(false);
    }
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setDragActive(false);
    const files = event.dataTransfer?.files;
    if (!files || files.length === 0) {
      setSubmitError(true);
      if (mode === "lecture") {
        setLectureFileStatus("No file was dropped.");
      } else {
        setLessonFileStatus("No files were dropped.");
      }
      return;
    }
    if (mode === "lecture") {
      if (files.length > 1) {
        setSubmitError(false);
        setSubmitStatus("Multiple files detected; using the first file only.");
      }
      applyLectureFile(files[0], "drop");
      return;
    }
    applyLessonFiles(files, "drop");
  };

  const renderDropzone = (currentMode) => {
    const isLectureMode = currentMode === "lecture";
    const inputRef = isLectureMode ? lectureInputRef : lessonInputRef;
    const onBrowse = () => inputRef.current?.click();

    return (
      <div
        className={cn(
          "relative flex-1 min-h-[280px] rounded-lg border-2 border-dashed flex flex-col items-center justify-center text-center p-6 cursor-pointer transition-colors",
          dragActive
            ? "border-primary bg-primary/5"
            : "border-border hover:border-muted-foreground/40 hover:bg-muted/30"
        )}
        role="button"
        tabIndex={0}
        aria-label={isLectureMode ? "Upload PDF file" : "Upload multiple PDF files"}
        onClick={onBrowse}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onBrowse();
          }
        }}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          setDragActive(false);
        }}
        onDrop={handleDrop}
      >
        <div className="w-14 h-14 rounded-xl bg-muted flex items-center justify-center mb-4">
          <CloudUpload className="w-6 h-6 text-muted-foreground" />
        </div>
        <p className="text-xl font-bold tracking-tight">
          {isLectureMode ? "Drag & drop your file here" : "Drag & drop your decks here"}
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          or <span className="text-primary font-semibold cursor-pointer">browse from your computer</span>
        </p>
        <Badge variant="secondary" className="mt-4 text-xs font-medium">
          {isLectureMode ? "Supports .pdf (Max 50MB)" : "2+ PDFs, 50MB each"}
        </Badge>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple={!isLectureMode}
          hidden
          onChange={(event) => {
            if (isLectureMode) {
              applyLectureFile(event.target.files && event.target.files[0], "picker");
            } else {
              applyLessonFiles(event.target.files || [], "picker");
            }
          }}
        />
      </div>
    );
  };

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>SlideParser - New Project</title>
      </Head>

      <div className="relative min-h-screen max-w-6xl mx-auto flex flex-col border-x bg-card">
        <header className="grid grid-cols-[auto_1fr_auto] items-center gap-3.5 px-5 py-3.5 border-b">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center overflow-hidden">
              <img
                src="/slideparser_logo.png"
                alt="SlideParser logo"
                className="block w-full h-full object-contain"
                loading="eager"
              />
            </div>
            <span className="text-lg font-extrabold tracking-tight">SlideParser</span>
            <Badge variant="secondary" className="text-[0.68rem] font-semibold">
              Studio
            </Badge>
          </div>

          <nav className="justify-self-center inline-flex items-center gap-1 p-1 rounded-full border bg-muted/50" aria-label="Primary">
            <Button variant="ghost" size="sm" className="rounded-full text-sm font-medium text-muted-foreground" asChild>
              <Link href="/">My Projects</Link>
            </Button>
            <Button variant="secondary" size="sm" className="rounded-full text-sm font-semibold" asChild>
              <Link href="/new-project">New Project</Link>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="rounded-full text-sm font-medium text-muted-foreground"
              type="button"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Account is not wired in this version.");
              }}
            >
              Account
            </Button>
          </nav>

          <Avatar
            className="justify-self-end cursor-pointer"
            onClick={() => {
              setSubmitError(false);
              setSubmitStatus("Profile is not wired in this version.");
            }}
          >
            <AvatarFallback className="bg-muted text-xs font-bold">J</AvatarFallback>
          </Avatar>
        </header>

        <main className="flex-1 p-4 min-h-0">
          <Tabs
            value={mode}
            onValueChange={(value) => {
              setMode(value);
              setDragActive(false);
              setSubmitError(false);
              setSubmitStatus("");
            }}
            className="space-y-4"
          >
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <div>
                <p className="text-[0.7rem] font-semibold tracking-widest uppercase text-muted-foreground">Mode</p>
                <h1 className="mt-1 text-2xl font-bold tracking-tight">Create New Project</h1>
              </div>
              <TabsList className="rounded-full p-1">
                <TabsTrigger value="lecture" className="rounded-full px-4">
                  Lecture Mode
                </TabsTrigger>
                <TabsTrigger value="lessons" className="rounded-full px-4">
                  Lesson Mode
                </TabsTrigger>
              </TabsList>
            </div>

            <TabsContent value="lecture" className="mt-0">
              <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(320px,1fr)] gap-4 min-h-0 lg:grid-cols-1">
                <Card className="flex flex-col">
                  <CardContent className="flex-1 flex flex-col p-5">
                    <div className="mb-3.5">
                      <Badge variant="outline" className="text-[0.7rem] font-bold tracking-wider uppercase">
                        Step 1
                      </Badge>
                      <h2 className="mt-2.5 text-2xl font-bold tracking-tight">Upload slides</h2>
                      <p className="mt-2 text-sm text-muted-foreground max-w-[44ch]">
                        Upload one presentation deck to run the lecture pipeline.
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2" aria-hidden="true">
                        <Badge variant="secondary" className="text-xs font-medium">PDF pipeline</Badge>
                        <Badge variant="secondary" className="text-xs font-medium">Single deck</Badge>
                      </div>
                    </div>

                    {renderDropzone("lecture")}

                    <p className={cn("mt-3 text-sm min-h-[20px]", submitError && !lectureValidation.ok ? "text-destructive" : "text-muted-foreground")}>
                      {lectureFileStatus}
                    </p>

                    <Card className="mt-4">
                      <CardHeader className="pb-3 pt-4 px-4">
                        <CardTitle className="text-base">Processing Options</CardTitle>
                      </CardHeader>
                      <CardContent className="px-4 pb-4 space-y-2.5">
                        <label className="flex items-center gap-3 rounded-md border p-3 cursor-pointer hover:bg-muted/50 transition-colors">
                          <Checkbox />
                          <span className="text-sm font-medium">Detect diagrams automatically</span>
                        </label>
                        <label className="flex items-center gap-3 rounded-md border p-3 cursor-pointer hover:bg-muted/50 transition-colors">
                          <Checkbox defaultChecked />
                          <span className="text-sm font-medium">Enhance low-res images</span>
                        </label>
                        <label className="flex items-center gap-3 rounded-md border p-3 cursor-pointer hover:bg-muted/50 transition-colors">
                          <Checkbox />
                          <span className="text-sm font-medium">Generate closed captions</span>
                        </label>
                      </CardContent>
                    </Card>
                  </CardContent>
                </Card>

                <Card className="flex flex-col">
                  <CardContent className="flex-1 flex flex-col p-5">
                    <div className="mb-3.5">
                      <Badge variant="outline" className="text-[0.7rem] font-bold tracking-wider uppercase">
                        Step 2
                      </Badge>
                      <h2 className="mt-2.5 text-2xl font-bold tracking-tight">Select Narration</h2>
                      <p className="mt-2 text-sm text-muted-foreground max-w-[44ch]">
                        Choose which TTS engine to use for this lecture job.
                      </p>
                    </div>

                    <div className="space-y-2.5" role="radiogroup" aria-label="Narration selection">
                      {NARRATION_PRESETS.map((preset) => {
                        const selected = preset.id === selectedNarrationPreset;
                        return (
                          <button
                            key={preset.id}
                            className={cn(
                              "w-full text-left rounded-lg border p-3.5 cursor-pointer transition-all",
                              selected
                                ? "border-primary bg-primary/5 ring-1 ring-primary"
                                : "hover:border-muted-foreground/30 hover:bg-muted/30"
                            )}
                            data-narration={preset.id}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            onClick={() => setSelectedNarrationPreset(preset.id)}
                          >
                            <div className="flex items-center gap-3">
                              <Avatar className="w-10 h-10">
                                <AvatarFallback className="bg-muted text-sm font-bold">
                                  {preset.avatar}
                                </AvatarFallback>
                              </Avatar>
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-bold">{preset.name}</p>
                                <p className="text-xs text-muted-foreground mt-0.5">{preset.subtitle}</p>
                              </div>
                              {selected && (
                                <div className="w-5 h-5 rounded-md bg-primary flex items-center justify-center">
                                  <Check className="w-3 h-3 text-primary-foreground" />
                                </div>
                              )}
                            </div>
                            <div className="mt-2.5 flex items-center gap-2.5">
                              <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-primary/70"
                                  style={{ width: preset.width }}
                                />
                              </div>
                              <span className="text-xs font-semibold text-muted-foreground tabular-nums">
                                {preset.length}
                              </span>
                            </div>
                          </button>
                        );
                      })}
                    </div>

                    <div className="mt-auto pt-5">
                      <Button className="w-full h-12 text-base font-bold" disabled={!canSubmit} onClick={() => void submitLecture()}>
                        {isSubmitting ? "Generating..." : "Generate Lecture"}
                      </Button>
                      <p className="mt-2.5 text-center text-xs text-muted-foreground">Estimated time: ~2 mins</p>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            <TabsContent value="lessons" className="mt-0">
              <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(320px,1fr)] gap-4 min-h-0 lg:grid-cols-1">
                <Card className="flex flex-col">
                  <CardContent className="flex-1 flex flex-col p-5">
                    <div className="mb-3.5">
                      <Badge variant="outline" className="text-[0.7rem] font-bold tracking-wider uppercase">
                        Step 1
                      </Badge>
                      <h2 className="mt-2.5 text-2xl font-bold tracking-tight">Upload multiple decks</h2>
                      <p className="mt-2 text-sm text-muted-foreground max-w-[48ch]">
                        Upload at least two presentations. Lesson Mode groups related slides across decks into lesson buckets.
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2" aria-hidden="true">
                        <Badge variant="secondary" className="text-xs font-medium">Multi-deck ingest</Badge>
                        <Badge variant="secondary" className="text-xs font-medium">Lesson buckets</Badge>
                        <Badge variant="secondary" className="text-xs font-medium">Read-only output</Badge>
                      </div>
                    </div>

                    {renderDropzone("lessons")}

                    <p className={cn("mt-3 text-sm min-h-[20px]", submitError && !lessonValidation.ok ? "text-destructive" : "text-muted-foreground")}>
                      {lessonFileStatus}
                    </p>

                    <Card className="mt-4">
                      <CardHeader className="pb-3 pt-4 px-4">
                        <CardTitle className="text-base">Selected Decks</CardTitle>
                      </CardHeader>
                      <CardContent className="px-4 pb-4">
                        {selectedLessonFiles.length ? (
                          <div className="space-y-2">
                            {selectedLessonFiles.map((file, index) => (
                              <div key={`${file.name}-${index}`} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                                <div className="min-w-0">
                                  <p className="text-sm font-medium truncate">{file.name}</p>
                                  <p className="text-xs text-muted-foreground">{formatFileSize(file.size)}</p>
                                </div>
                                <Badge variant="outline" className="text-[0.68rem]">Deck {index + 1}</Badge>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-sm text-muted-foreground">No decks selected yet.</p>
                        )}
                      </CardContent>
                    </Card>
                  </CardContent>
                </Card>

                <Card className="flex flex-col">
                  <CardContent className="flex-1 flex flex-col p-5">
                    <div className="mb-3.5">
                      <Badge variant="outline" className="text-[0.7rem] font-bold tracking-wider uppercase">
                        Step 2
                      </Badge>
                      <h2 className="mt-2.5 text-2xl font-bold tracking-tight">Build lesson buckets</h2>
                      <p className="mt-2 text-sm text-muted-foreground max-w-[42ch]">
                        The backend will create slide cards, cluster related slides, and label each lesson candidate.
                      </p>
                    </div>

                    <div className="space-y-3">
                      <div className="rounded-lg border p-3.5">
                        <p className="text-sm font-semibold">Pipeline</p>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {useLessonMockMode
                            ? "Local mock mode renders slides and generates lightweight lesson buckets without GCS, upstream processing, or LLM calls."
                            : "Multi-deck upload, per-slide metadata, embeddings, clustering, and lesson labels."}
                        </p>
                      </div>
                      <div className="rounded-lg border p-3.5">
                        <p className="text-sm font-semibold">Output</p>
                        <p className="mt-1 text-sm text-muted-foreground">
                          A course page with ordered lessons and their assigned slide thumbnails.
                        </p>
                      </div>
                      <div className="rounded-lg border p-3.5">
                        <p className="text-sm font-semibold">Excluded for V1</p>
                        <p className="mt-1 text-sm text-muted-foreground">
                          No personalization, quizzes, narration, or editing tools in this mode.
                        </p>
                      </div>
                      <label className="flex items-center gap-3 rounded-lg border p-3.5 cursor-pointer hover:bg-muted/40 transition-colors">
                        <Checkbox
                          checked={useLessonMockMode}
                          onCheckedChange={(checked) => setUseLessonMockMode(checked === true)}
                        />
                        <div>
                          <p className="text-sm font-semibold">Use local test mode</p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            Recommended while testing. Uncheck to run the full upstream lesson pipeline.
                          </p>
                        </div>
                      </label>
                    </div>

                    <div className="mt-auto pt-5">
                      <Button className="w-full h-12 text-base font-bold" disabled={!canSubmit} onClick={() => void submitLessons()}>
                        {isSubmitting ? "Building..." : "Build Lessons"}
                      </Button>
                      <p className="mt-2.5 text-center text-xs text-muted-foreground">Estimated time: depends on total slide count</p>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>
          </Tabs>

          <div className="mt-4">
            <p
              className={cn(
                "text-center text-sm min-h-[22px]",
                submitError ? "text-destructive" : "text-primary"
              )}
              aria-live="polite"
            >
              {submitStatus}
            </p>
            <p className={cn("mt-2 text-center text-sm min-h-[20px]", submitError && !activeValidation.ok ? "text-destructive" : "text-muted-foreground")}>
              {activeFileStatus}
            </p>
          </div>
        </main>

        <footer className="flex justify-between items-center gap-3 px-5 py-3.5 border-t text-sm text-muted-foreground">
          <span>&copy; 2026 SlideParser</span>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="text-xs text-muted-foreground"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Help is not wired in this version.");
              }}
            >
              Help
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-xs text-muted-foreground"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Terms is not wired in this version.");
              }}
            >
              Terms
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-xs text-muted-foreground"
              onClick={() => {
                setSubmitError(false);
                setSubmitStatus("Privacy is not wired in this version.");
              }}
            >
              Privacy
            </Button>
          </div>
        </footer>
      </div>
    </>
  );
}
