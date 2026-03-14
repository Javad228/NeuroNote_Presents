import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import {
  LayoutDashboard,
  Clock,
  Star,
  Archive,
  Settings,
  HelpCircle,
  Plus,
  Sun,
  Moon,
  Search,
} from "lucide-react";

function relativeTime(isoValue) {
  if (!isoValue) {
    return "Unknown time";
  }
  const then = new Date(isoValue).getTime();
  if (Number.isNaN(then)) {
    return "Unknown time";
  }

  const deltaSec = Math.floor((Date.now() - then) / 1000);
  if (deltaSec < 60) {
    return "Just now";
  }
  if (deltaSec < 3600) {
    return `${Math.floor(deltaSec / 60)} min ago`;
  }
  if (deltaSec < 86400) {
    return `${Math.floor(deltaSec / 3600)} hr ago`;
  }
  if (deltaSec < 604800) {
    return `${Math.floor(deltaSec / 86400)} day ago`;
  }
  return new Date(then).toLocaleDateString();
}

function JobCard({ job, onOpen }) {
  const title = job.title || job.job_id;
  const timeLabel = relativeTime(job.updated_at);
  const status = job.status === "complete" ? "complete" : "partial";
  const statusLabel = status === "complete" ? "READY" : "PARTIAL";
  const isLessonJob = job.job_kind === "lesson_course";
  const pageCount = Number.isInteger(job.page_count) ? `Pages: ${job.page_count}` : "Pages: -";
  const secondaryCount = isLessonJob
    ? `Decks: ${Number.isInteger(job.deck_count) ? job.deck_count : "-"}`
    : `Chunks: ${Number.isInteger(job.chunk_count) ? job.chunk_count : "-"}`;
  const tertiaryCount = isLessonJob
    ? `Lessons: ${Number.isInteger(job.lesson_count) ? job.lesson_count : "-"}`
    : null;

  return (
    <Card
      className="group cursor-pointer overflow-hidden transition-all hover:shadow-md hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      role="listitem"
      aria-label={title}
      data-job-id={job.job_id || ""}
      tabIndex={0}
      onClick={() => onOpen(job.job_id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(job.job_id);
        }
      }}
    >
      {job.thumbnail_url ? (
        <img
          className="block w-full h-[132px] object-cover border-b border-border"
          src={job.thumbnail_url}
          alt={title}
          loading="lazy"
        />
      ) : (
        <div
          className="w-full h-[132px] bg-muted border-b border-border"
          aria-hidden="true"
        />
      )}
      <CardContent className="p-3.5">
        <h3 className="font-semibold text-sm leading-tight line-clamp-2">{title}</h3>
        <p className="mt-1.5 text-xs text-muted-foreground">{timeLabel}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {pageCount} · {secondaryCount}{tertiaryCount ? ` · ${tertiaryCount}` : ""}
        </p>
        <Badge
          variant={status === "complete" ? "default" : "secondary"}
          className={`mt-2.5 text-[0.65rem] font-bold tracking-wider ${
            status === "complete"
              ? "bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/40 dark:text-emerald-400"
              : "bg-amber-100 text-amber-700 hover:bg-amber-100 dark:bg-amber-900/40 dark:text-amber-400"
          }`}
        >
          {statusLabel}
        </Badge>
        <Badge variant="outline" className="mt-2 ml-2 text-[0.65rem] font-bold tracking-wider">
          {isLessonJob ? "LESSONS" : "LECTURE"}
        </Badge>
      </CardContent>
    </Card>
  );
}

function CreateCard({ onCreate }) {
  return (
    <Card
      className="min-h-[232px] border-dashed border-2 flex flex-col items-center justify-center text-center cursor-pointer transition-all hover:shadow-md hover:-translate-y-0.5 hover:border-muted-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      role="listitem"
      aria-label="Create new project"
      tabIndex={0}
      onClick={onCreate}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onCreate();
        }
      }}
    >
      <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center mb-3">
        <Plus className="w-5 h-5 text-muted-foreground" />
      </div>
      <p className="font-bold text-sm">Create New</p>
      <p className="text-xs text-muted-foreground mt-1">From PDF or Slides</p>
    </Card>
  );
}

const NAV_ITEMS = [
  { label: "Dashboard", icon: LayoutDashboard, active: true },
  { label: "Recent", icon: Clock },
  { label: "Favorites", icon: Star },
  { label: "Archived", icon: Archive },
];

const NAV_BOTTOM = [
  { label: "Settings", icon: Settings },
  { label: "Help & Tutorials", icon: HelpCircle },
];

export default function DashboardPage({ theme = "light", setTheme }) {
  const router = useRouter();
  const [jobs, setJobs] = useState([]);
  const [statusText, setStatusText] = useState("Loading jobs...");
  const [searchQuery, setSearchQuery] = useState("");
  const isDark = theme === "dark";

  useEffect(() => {
    let cancelled = false;
    async function loadJobs() {
      setStatusText("Loading jobs...");
      try {
        const response = await fetch("/api/jobs");
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload?.detail || `Request failed: ${response.status}`);
        }
        const nextJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
        if (cancelled) {
          return;
        }
        setJobs(nextJobs);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setJobs([]);
        setStatusText("Failed to load jobs.");
      }
    }
    void loadJobs();
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredJobs = jobs.filter((job) => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) {
      return true;
    }
    const hay = `${job.title || ""} ${job.job_id || ""} ${job.input_pdf_name || ""} ${job.job_kind || ""}`.toLowerCase();
    return hay.includes(q);
  });

  useEffect(() => {
    if (!jobs.length) {
      if (statusText !== "Failed to load jobs.") {
        setStatusText("No jobs yet.");
      }
      return;
    }
    setStatusText(`${filteredJobs.length} job${filteredJobs.length === 1 ? "" : "s"} shown`);
  }, [jobs, filteredJobs.length, statusText]);

  const openJob = (jobId) => {
    if (!jobId) {
      return;
    }
    const job = jobs.find((item) => item.job_id === jobId);
    const route = job?.job_kind === "lesson_course" ? "/course/" : "/lecture/";
    void router.push(`${route}${encodeURIComponent(jobId)}`);
  };

  const toggleTheme = () => {
    if (typeof setTheme === "function") {
      setTheme(isDark ? "light" : "dark");
      return;
    }
    if (typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent("nn:theme:set", {
          detail: { theme: isDark ? "light" : "dark" },
        })
      );
    }
  };

  return (
    <>
      <Head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>SlideParser Dashboard</title>
      </Head>

      <div className="relative min-h-screen grid grid-cols-[280px_minmax(0,1fr)] gap-4 p-4 lg:grid-cols-[280px_minmax(0,1fr)] md:grid-cols-1">
        {/* Sidebar */}
        <aside className="flex flex-col rounded-lg border bg-card p-3.5 md:min-h-0 min-h-[calc(100vh-2rem)]">
          <div className="flex items-center gap-2.5 px-2 pb-3 mb-1.5 border-b">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center overflow-hidden">
              <img
                src="/slideparser_logo.png"
                alt="SlideParser logo"
                className="block w-full h-full object-contain"
                loading="eager"
              />
            </div>
            <span className="font-extrabold text-sm tracking-tight">SlideParser</span>
          </div>

          <div className="mt-2">
            <p className="px-2.5 text-[0.7rem] font-semibold tracking-widest uppercase text-muted-foreground mb-1">
              My Projects
            </p>
            <div className="space-y-0.5">
              {NAV_ITEMS.map((item) => (
                <Button
                  key={item.label}
                  variant={item.active ? "secondary" : "ghost"}
                  className={`w-full justify-start gap-2.5 h-9 text-sm font-medium ${
                    item.active ? "font-semibold" : "text-muted-foreground"
                  }`}
                  type="button"
                >
                  <item.icon className="w-4 h-4" />
                  {item.label}
                </Button>
              ))}
            </div>
          </div>

          <div className="mt-auto pt-3 border-t space-y-0.5">
            {NAV_BOTTOM.map((item) => (
              <Button
                key={item.label}
                variant="ghost"
                className="w-full justify-start gap-2.5 h-9 text-sm font-medium text-muted-foreground"
                type="button"
              >
                <item.icon className="w-4 h-4" />
                {item.label}
              </Button>
            ))}
          </div>

          <Separator className="my-3" />

          <div className="flex items-center gap-3 rounded-md border p-2.5">
            <Avatar className="w-8 h-8">
              <AvatarFallback className="bg-muted text-xs font-bold">J</AvatarFallback>
            </Avatar>
            <div className="min-w-0">
              <p className="text-sm font-semibold leading-tight truncate">Local User</p>
              <p className="text-xs text-muted-foreground">Free Plan</p>
            </div>
          </div>
        </aside>

        {/* Main content */}
        <main className="min-w-0 grid grid-rows-[auto_minmax(0,1fr)] gap-4">
          {/* Hero / topbar */}
          <section className="rounded-lg border bg-card p-4" aria-label="Dashboard overview">
            <header className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <p className="text-[0.7rem] font-semibold tracking-widest uppercase text-muted-foreground">
                  Workspace
                </p>
                <h1 className="mt-1 text-2xl font-bold tracking-tight">Dashboard</h1>
                <p className="mt-1 text-sm text-muted-foreground">overview</p>
              </div>

              <div className="flex items-center gap-2.5 flex-wrap">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    className="pl-9 w-[240px] max-w-full"
                    type="text"
                    placeholder="Search projects..."
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={toggleTheme}
                  aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                >
                  {isDark ? <Sun className="w-4 h-4 mr-1.5" /> : <Moon className="w-4 h-4 mr-1.5" />}
                  {isDark ? "Light" : "Dark"}
                </Button>
                <Button size="sm" onClick={() => void router.push("/new-project")}>
                  <Plus className="w-4 h-4 mr-1.5" />
                  New Project
                </Button>
              </div>
            </header>
          </section>

          {/* Jobs grid */}
          <section className="rounded-lg border bg-card p-4 min-h-0">
            <div className="flex items-end justify-between gap-3">
              <div>
                <p className="text-[0.7rem] font-semibold tracking-widest uppercase text-muted-foreground">
                  Library
                </p>
                <h2 className="mt-1 text-lg font-bold tracking-tight">Recent Jobs</h2>
              </div>
              <Badge variant="secondary" className="text-xs font-medium">
                Local cache
              </Badge>
            </div>

            <p className="mt-2.5 text-sm text-muted-foreground">{statusText}</p>

            <div
              className="mt-3.5 grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3.5"
              aria-live="polite"
            >
              {filteredJobs.map((job, index) => (
                <JobCard key={job.job_id || `job-${index}`} job={job} onOpen={openJob} />
              ))}
              <CreateCard onCreate={() => void router.push("/new-project")} />
            </div>
          </section>
        </main>
      </div>
    </>
  );
}
