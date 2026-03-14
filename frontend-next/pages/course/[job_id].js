import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { ArrowLeft, BookOpen, Layers3 } from "lucide-react";

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

function buildApiUrl(path) {
  const origin = resolveBackendOrigin();
  return origin ? `${origin}${path}` : path;
}

function CoursePage({ theme = "light", setTheme }) {
  const router = useRouter();
  const jobId = typeof router.query.job_id === "string" ? router.query.job_id : "";
  const [payload, setPayload] = useState(null);
  const [statusText, setStatusText] = useState("Loading course...");
  const [errorText, setErrorText] = useState("");
  const [learningLessonId, setLearningLessonId] = useState("");

  useEffect(() => {
    if (!jobId) {
      return undefined;
    }
    let cancelled = false;

    async function loadCourse() {
      setStatusText("Loading course...");
      setErrorText("");
      try {
        const response = await fetch(buildApiUrl(`/api/jobs/${encodeURIComponent(jobId)}/lessons`));
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data?.detail || `Request failed with status ${response.status}.`);
        }
        if (cancelled) {
          return;
        }
        setPayload(data);
        const count = Array.isArray(data.lessons) ? data.lessons.length : 0;
        setStatusText(`${count} lesson${count === 1 ? "" : "s"} generated`);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setPayload(null);
        setErrorText(error?.message || "Unable to load lesson payload.");
        setStatusText("Course load failed.");
      }
    }

    void loadCourse();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const slidesById = useMemo(() => {
    const map = new Map();
    const slides = Array.isArray(payload?.slides) ? payload.slides : [];
    for (const slide of slides) {
      if (slide?.slide_id) {
        map.set(slide.slide_id, slide);
      }
    }
    return map;
  }, [payload]);

  async function handleLearn(lesson) {
    if (!jobId || !lesson?.lesson_id || learningLessonId) {
      return;
    }

    setLearningLessonId(lesson.lesson_id);
    setErrorText("");
    setStatusText(`Generating lecture for ${lesson.title || "lesson"}...`);
    try {
      const response = await fetch(
        buildApiUrl(`/api/jobs/${encodeURIComponent(jobId)}/lessons/${encodeURIComponent(lesson.lesson_id)}/learn`),
        { method: "POST" }
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || `Request failed with status ${response.status}.`);
      }
      const lectureJobId = typeof data?.job_id === "string" ? data.job_id : "";
      if (!lectureJobId) {
        throw new Error("Lecture job response did not include a job_id.");
      }
      await router.push(`/lecture/${encodeURIComponent(lectureJobId)}`);
    } catch (error) {
      setErrorText(error?.message || "Unable to generate a lecture for this lesson.");
      setStatusText("Course load failed.");
      setLearningLessonId("");
    }
  }

  function renderSlideCard(slideId) {
    const slide = slidesById.get(slideId);
    const slideTitle = slide?.title || slide?.page_title || slideId;

    return (
      <div key={slideId} className="rounded-lg border overflow-hidden bg-background/60">
        {slide?.image_url ? (
          <img
            src={slide.image_url}
            alt={slideTitle}
            className="block w-full h-36 object-cover border-b"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-36 bg-muted border-b" aria-hidden="true" />
        )}
        <div className="p-3">
          <p className="text-xs font-semibold tracking-widest uppercase text-muted-foreground">
            {slide?.deck_title || slide?.deck_id || "Deck"} · Slide {slide?.slide_number || "-"}
          </p>
          <p className="mt-1 text-sm font-semibold leading-tight">{slideTitle}</p>
        </div>
      </div>
    );
  }

  function relationLabel(relation) {
    if (relation === "anchor") return "Anchor block";
    if (relation === "same_lesson") return "Core block";
    if (relation === "example_support") return "Example support";
    if (relation === "recap_review") return "Recap review";
    return relation || "Block";
  }

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{payload?.course_title || "Course"} - SlideParser</title>
      </Head>

      <div className="relative min-h-screen max-w-7xl mx-auto flex flex-col border-x bg-card">
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
              Course View
            </Badge>
          </div>

          <div className="justify-self-center flex items-center gap-2">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/">
                <ArrowLeft className="w-4 h-4 mr-1.5" />
                Dashboard
              </Link>
            </Button>
          </div>

          <Avatar className="justify-self-end cursor-pointer" onClick={() => setTheme?.(theme === "dark" ? "light" : "dark")}>
            <AvatarFallback className="bg-muted text-xs font-bold">J</AvatarFallback>
          </Avatar>
        </header>

        <main className="flex-1 p-4 space-y-4">
          <section className="rounded-lg border bg-card p-5">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <p className="text-[0.7rem] font-semibold tracking-widest uppercase text-muted-foreground">Generated Course</p>
                <h1 className="mt-1 text-3xl font-bold tracking-tight">
                  {payload?.course_title || (errorText ? "Course unavailable" : "Loading...")}
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">{statusText}</p>
              </div>
              <div className="flex gap-2 flex-wrap">
                <Badge variant="outline" className="text-xs font-semibold">
                  <Layers3 className="w-3.5 h-3.5 mr-1.5" />
                  {payload?.deck_count ?? "-"} decks
                </Badge>
                <Badge variant="outline" className="text-xs font-semibold">
                  <BookOpen className="w-3.5 h-3.5 mr-1.5" />
                  {payload?.lesson_count ?? "-"} lessons
                </Badge>
                <Badge variant="outline" className="text-xs font-semibold">
                  {payload?.slide_count ?? "-"} slides
                </Badge>
                <Badge variant="outline" className="text-xs font-semibold">
                  {payload?.pipeline_version || "legacy"}
                </Badge>
              </div>
            </div>
            {errorText ? (
              <p className="mt-3 text-sm text-destructive">{errorText}</p>
            ) : null}
          </section>

          {!payload && !errorText ? (
            <div className="grid gap-4">
              {[0, 1, 2].map((index) => (
                <Card key={index}>
                  <CardContent className="p-5 space-y-3">
                    <Skeleton className="h-6 w-48" />
                    <Skeleton className="h-4 w-full" />
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
                      {[0, 1, 2].map((thumb) => (
                        <Skeleton key={thumb} className="h-32 w-full rounded-lg" />
                      ))}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : null}

          {payload && Array.isArray(payload.lessons) ? (
            <section className="space-y-4">
              {payload.lessons.map((lesson) => (
                <Card key={lesson.lesson_id}>
                  <CardContent className="p-5">
                    <div className="flex items-start justify-between gap-3 flex-wrap">
                      <div>
                        <p className="text-[0.72rem] font-semibold tracking-widest uppercase text-muted-foreground">
                          Lesson {lesson.order_index}
                        </p>
                        <h2 className="mt-1 text-xl font-bold tracking-tight">{lesson.title}</h2>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary" className="text-xs font-semibold">
                          {(lesson.slide_ids || []).length} slides
                        </Badge>
                        <Button
                          size="sm"
                          onClick={() => handleLearn(lesson)}
                          disabled={learningLessonId === lesson.lesson_id}
                        >
                          {learningLessonId === lesson.lesson_id ? "Generating..." : "Learn"}
                        </Button>
                      </div>
                    </div>

                    <div className="mt-4 grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
                      {Array.isArray(lesson.blocks) && lesson.blocks.length ? (
                        <div className="col-span-full space-y-3">
                          {lesson.blocks.map((block) => {
                            const firstSlide = Array.isArray(block.slide_ids) && block.slide_ids.length
                              ? slidesById.get(block.slide_ids[0])
                              : null;
                            const lastSlide = Array.isArray(block.slide_ids) && block.slide_ids.length
                              ? slidesById.get(block.slide_ids[block.slide_ids.length - 1])
                              : null;
                            return (
                              <div key={block.segment_id} className="rounded-xl border bg-background/40 p-4">
                                <div className="flex items-start justify-between gap-3 flex-wrap">
                                  <div>
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <h3 className="text-base font-semibold tracking-tight">{block.title}</h3>
                                      <Badge variant={block.is_anchor ? "secondary" : "outline"} className="text-[0.68rem] font-semibold">
                                        {relationLabel(block.relation_to_anchor)}
                                      </Badge>
                                      <Badge variant="outline" className="text-[0.68rem] font-medium capitalize">
                                        {block.segment_kind?.replaceAll("_", " ") || "segment"}
                                      </Badge>
                                    </div>
                                  </div>
                                  <div className="text-right text-xs text-muted-foreground">
                                    <div>{block.deck_title || block.deck_id}</div>
                                    <div>
                                      {firstSlide?.slide_number || "-"}
                                      {" to "}
                                      {lastSlide?.slide_number || "-"}
                                    </div>
                                  </div>
                                </div>
                                <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-3">
                                  {(block.slide_ids || []).map((slideId) => renderSlideCard(slideId))}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        (lesson.slide_ids || []).map((slideId) => renderSlideCard(slideId))
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </section>
          ) : null}
        </main>
      </div>
    </>
  );
}

export default CoursePage;
