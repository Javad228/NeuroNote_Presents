from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from ..config import AppConfig
from .jobs import JobsService
from .lecture import LectureService
from .transcript_audio import TranscriptAudioService


logger = logging.getLogger(__name__)

_FRAME_WIDTH = 1920
_FRAME_HEIGHT = 1080
_MARGIN = 28
_CONTENT_GAP = 24
_HEADER_HEIGHT = 78
_SCRIPT_PANEL_WIDTH = 620
_CARD_RADIUS = 26
_SLIDE_PANEL_BG = (5, 8, 12, 255)
_PAGE_BG = (242, 245, 250, 255)
_CARD_BG = (255, 255, 255, 255)
_CARD_BORDER = (218, 225, 236, 255)
_TEXT_PRIMARY = (15, 23, 42, 255)
_TEXT_SECONDARY = (100, 116, 139, 255)
_TEXT_MUTED = (148, 163, 184, 255)
_ACCENT = (37, 99, 235, 255)
_ACCENT_BG = (219, 234, 254, 255)
_ACCENT_SOFT = (191, 219, 254, 255)
_VISUAL_ACCENT = (56, 188, 255, 255)
_VISUAL_TINT = (110, 233, 255, 36)
_UNDERLAY_TINT = (4, 8, 16, 174)
_SHADOW = (15, 23, 42, 120)
_LIFT_DX = 0
_LIFT_DY = -12
_DEFAULT_TTS_PROVIDER = "openai"
_DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
_DEFAULT_TTS_VOICE = "marin"
_TIMESTAMPS_FILE = "transcript_audio_timestamps.json"
_EXPORTS_DIR = "exports"
_VIDEO_FILE = "lecture_with_script.mp4"
_METADATA_FILE = "lecture_with_script.json"

_REGULAR_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_BOLD_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_MONO_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)

try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9
    _LANCZOS = Image.LANCZOS


class LectureExportService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.jobs_service = JobsService(config)
        self.lecture_service = LectureService(config)
        self.transcript_audio_service = TranscriptAudioService(config)
        self._font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def get_video_export_path(self, job_id: str) -> Path | None:
        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            return None
        candidate = job_dir / _EXPORTS_DIR / _VIDEO_FILE
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    def export_job_video(self, job_id: str) -> dict[str, Any]:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg is not installed or not on PATH.")

        job_dir = self.jobs_service.resolve_job_dir(job_id)
        if job_dir is None:
            raise FileNotFoundError(f"Job directory not found: {job_id}")

        audio_meta = self._ensure_openai_audio(job_id, job_dir)
        lecture = self.lecture_service.get_lecture_payload(job_id)
        if lecture is None:
            raise RuntimeError(f"Lecture payload could not be loaded for job '{job_id}'.")

        entries = self._build_timeline_entries(lecture)
        if not entries:
            raise RuntimeError("No scripted steps were found for this lecture job.")

        audio_path = self.lecture_service.get_transcript_audio_path(job_id)
        if audio_path is None or not audio_path.exists():
            raise RuntimeError("Narration audio is missing after generation.")

        exports_dir = job_dir / _EXPORTS_DIR
        exports_dir.mkdir(parents=True, exist_ok=True)
        final_video_path = exports_dir / _VIDEO_FILE
        metadata_path = exports_dir / _METADATA_FILE

        with tempfile.TemporaryDirectory(prefix=f"{job_id}_video_", dir=str(exports_dir)) as tmp_root_str:
            tmp_root = Path(tmp_root_str)
            frames_dir = tmp_root / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            frame_paths = self._render_frames(job_id, lecture, entries, frames_dir)
            concat_path = self._write_concat_file(entries, frame_paths, tmp_root / "frames.txt")
            tmp_output = tmp_root / "lecture.mp4"
            self._run_ffmpeg(
                ffmpeg_path=ffmpeg_path,
                concat_path=concat_path,
                audio_path=audio_path,
                output_path=tmp_output,
            )
            tmp_output.replace(final_video_path)

        payload = {
            "job_id": job_id,
            "video_url": f"/api/jobs/{job_id}/video",
            "video_file": final_video_path.name,
            "video_path": str(final_video_path),
            "audio_path": str(audio_path),
            "audio_generated": bool(audio_meta.get("audio_generated")),
            "audio_provider": audio_meta.get("provider"),
            "audio_model": audio_meta.get("model"),
            "audio_voice": audio_meta.get("voice"),
            "step_count": len(entries),
            "duration_ms": int(entries[-1]["end_ms"]),
            "title": lecture.get("title"),
        }
        metadata_path.write_text(json.dumps(payload, indent=2))
        return payload

    def _ensure_openai_audio(self, job_id: str, job_dir: Path) -> dict[str, Any]:
        timestamps_path = job_dir / _TIMESTAMPS_FILE
        audio_path = self.lecture_service.get_transcript_audio_path(job_id)
        metadata = self._read_json(timestamps_path)

        provider = str((metadata or {}).get("provider") or "").strip().lower()
        model = str((metadata or {}).get("model") or "").strip()
        voice = str((metadata or {}).get("voice") or "").strip()
        timing_steps = (metadata or {}).get("steps")

        if (
            audio_path is not None
            and audio_path.exists()
            and isinstance(timing_steps, list)
            and timing_steps
            and provider == _DEFAULT_TTS_PROVIDER
            and model == _DEFAULT_TTS_MODEL
        ):
            return {
                "audio_generated": False,
                "provider": provider,
                "model": model,
                "voice": voice or _DEFAULT_TTS_VOICE,
            }

        resolved_voice = (
            os.getenv("LECTURE_EXPORT_TTS_VOICE", "").strip()
            or os.getenv("TRANSCRIPT_TTS_VOICE", "").strip()
            or _DEFAULT_TTS_VOICE
        )
        logger.info(
            "lecture_export.audio_generate_start job=%s provider=%s model=%s voice=%s",
            job_id,
            _DEFAULT_TTS_PROVIDER,
            _DEFAULT_TTS_MODEL,
            resolved_voice,
        )
        result = self.transcript_audio_service.generate_for_job(
            job_id,
            tts_provider=_DEFAULT_TTS_PROVIDER,
            tts_model=_DEFAULT_TTS_MODEL,
            tts_voice=resolved_voice,
        )
        logger.info("lecture_export.audio_generate_done job=%s", job_id)
        return {
            "audio_generated": True,
            "provider": str(result.get("provider") or _DEFAULT_TTS_PROVIDER),
            "model": str(result.get("model") or _DEFAULT_TTS_MODEL),
            "voice": str(result.get("voice") or resolved_voice),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text())
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _format_clock(ms: int) -> str:
        total_seconds = max(0, int(round(ms / 1000)))
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _truncate_text(text: Any, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)].rstrip() + "..."

    @staticmethod
    def _valid_audio_timing(step: dict[str, Any]) -> tuple[int, int] | None:
        try:
            start_ms = int(step.get("audio_start_ms"))
            end_ms = int(step.get("audio_end_ms"))
        except Exception:
            return None
        if end_ms <= start_ms:
            return None
        return start_ms, end_ms

    @staticmethod
    def _normalize_dwell_ms(step: dict[str, Any]) -> int:
        try:
            dwell_ms = int(step.get("dwell_ms"))
        except Exception:
            dwell_ms = 3500
        return dwell_ms if dwell_ms > 0 else 3500

    @classmethod
    def _build_timeline_entries(cls, lecture: dict[str, Any]) -> list[dict[str, Any]]:
        slides = lecture.get("slides")
        if not isinstance(slides, list):
            return []

        entries: list[dict[str, Any]] = []
        cursor = 0
        for slide_index, slide in enumerate(slides):
            if not isinstance(slide, dict):
                continue
            slide_number = slide.get("slide_number")
            try:
                normalized_slide_number = int(slide_number)
            except Exception:
                normalized_slide_number = slide_index + 1
            image_name = str(slide.get("image_name") or f"page_{normalized_slide_number:03d}")
            steps = slide.get("steps")
            if not isinstance(steps, list):
                continue

            for step_index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                line = str(step.get("line") or "").strip()
                if not line:
                    continue

                timing = cls._valid_audio_timing(step)
                if timing is not None:
                    start_ms, end_ms = timing
                else:
                    start_ms = cursor
                    end_ms = start_ms + cls._normalize_dwell_ms(step)

                duration_ms = max(250, end_ms - start_ms)
                entry = {
                    "global_index": len(entries),
                    "slide_index": slide_index,
                    "slide_number": normalized_slide_number,
                    "image_name": image_name,
                    "slide_name": f"{image_name}.png",
                    "step_index": step_index,
                    "step_number": int(step.get("step_number") or (step_index + 1)),
                    "line": line,
                    "start_ms": start_ms,
                    "end_ms": start_ms + duration_ms,
                    "duration_ms": duration_ms,
                }
                entries.append(entry)
                cursor = max(cursor, entry["end_ms"])

        if not entries:
            return []

        base_offset = int(entries[0]["start_ms"])
        if base_offset > 0:
            for entry in entries:
                entry["start_ms"] = max(0, int(entry["start_ms"]) - base_offset)
                entry["end_ms"] = max(entry["start_ms"], int(entry["end_ms"]) - base_offset)
                entry["duration_ms"] = max(250, int(entry["end_ms"]) - int(entry["start_ms"]))

        for entry in entries:
            entry["timestamp_label"] = cls._format_clock(int(entry["start_ms"]))

        return entries

    def _render_frames(
        self,
        job_id: str,
        lecture: dict[str, Any],
        entries: list[dict[str, Any]],
        frames_dir: Path,
    ) -> list[Path]:
        frame_paths: list[Path] = []
        for entry in entries:
            frame = self._render_frame(job_id, lecture, entries, entry)
            frame_path = frames_dir / f"frame_{int(entry['global_index']) + 1:04d}.png"
            frame.convert("RGB").save(frame_path, format="PNG")
            frame_paths.append(frame_path)
        return frame_paths

    def _render_frame(
        self,
        job_id: str,
        lecture: dict[str, Any],
        entries: list[dict[str, Any]],
        entry: dict[str, Any],
    ) -> Image.Image:
        frame = Image.new("RGBA", (_FRAME_WIDTH, _FRAME_HEIGHT), _PAGE_BG)
        draw = ImageDraw.Draw(frame)

        title_font = self._load_font(28, bold=True)
        header_meta_font = self._load_font(18)
        panel_title_font = self._load_font(26, bold=True)
        badge_font = self._load_font(16, bold=True)
        body_font = self._load_font(24)
        body_bold_font = self._load_font(24, bold=True)
        time_font = self._load_font(18, mono=True)
        small_font = self._load_font(18)
        small_bold_font = self._load_font(18, bold=True)

        left_x1 = _MARGIN
        left_y1 = _HEADER_HEIGHT + _MARGIN
        right_x2 = _FRAME_WIDTH - _MARGIN
        right_x1 = right_x2 - _SCRIPT_PANEL_WIDTH
        left_x2 = right_x1 - _CONTENT_GAP
        content_y2 = _FRAME_HEIGHT - _MARGIN

        draw.rounded_rectangle(
            (left_x1, 14, right_x2, _HEADER_HEIGHT),
            radius=22,
            fill=_CARD_BG,
            outline=_CARD_BORDER,
            width=2,
        )
        title = self._truncate_text(lecture.get("title") or entry["image_name"], 92)
        draw.text((left_x1 + 24, 28), title, font=title_font, fill=_TEXT_PRIMARY)
        subtitle = f"Step {int(entry['global_index']) + 1} of {len(entries)}"
        subtitle_width = self._measure_text(draw, subtitle, header_meta_font)
        draw.text((right_x2 - subtitle_width - 24, 34), subtitle, font=header_meta_font, fill=_TEXT_SECONDARY)

        draw.rounded_rectangle(
            (left_x1, left_y1, left_x2, content_y2),
            radius=_CARD_RADIUS,
            fill=_SLIDE_PANEL_BG,
        )
        draw.rounded_rectangle(
            (right_x1, left_y1, right_x2, content_y2),
            radius=_CARD_RADIUS,
            fill=_CARD_BG,
            outline=_CARD_BORDER,
            width=2,
        )

        slide = self._resolve_slide(lecture, int(entry["slide_index"]))
        slide_image = self._render_slide_for_entry(job_id, slide, entry)
        self._paste_slide_image(frame, slide_image, (left_x1, left_y1, left_x2, content_y2))

        slide_badge_text = f"Slide {int(entry['slide_number'])}"
        badge_width = self._measure_text(draw, slide_badge_text, badge_font) + 28
        badge_box = (left_x1 + 24, left_y1 + 22, left_x1 + 24 + badge_width, left_y1 + 22 + 34)
        draw.rounded_rectangle(badge_box, radius=18, fill=(255, 255, 255, 28))
        draw.text((badge_box[0] + 14, badge_box[1] + 7), slide_badge_text, font=badge_font, fill=(247, 250, 252, 255))

        panel_padding_x = 28
        panel_top = left_y1 + 26
        draw.text((right_x1 + panel_padding_x, panel_top), "Script", font=panel_title_font, fill=_TEXT_PRIMARY)

        badge_text = "Narrated export"
        badge_width = self._measure_text(draw, badge_text, badge_font) + 24
        badge_y1 = panel_top + 2
        badge_y2 = badge_y1 + 28
        badge_x2 = right_x2 - panel_padding_x
        badge_x1 = badge_x2 - badge_width
        draw.rounded_rectangle((badge_x1, badge_y1, badge_x2, badge_y2), radius=16, fill=_ACCENT_BG)
        draw.text((badge_x1 + 12, badge_y1 + 6), badge_text, font=badge_font, fill=_ACCENT)

        meta_text = f"{entry['timestamp_label']}  |  {self._format_clock(int(entries[-1]['end_ms']))} total"
        draw.text((right_x1 + panel_padding_x, panel_top + 38), meta_text, font=small_font, fill=_TEXT_SECONDARY)

        body_top = panel_top + 84
        body_bottom = content_y2 - 72
        body_left = right_x1 + 18
        body_right = right_x2 - 18
        body_width = body_right - body_left
        body_height = body_bottom - body_top

        item_layouts = self._build_script_window_layouts(
            draw=draw,
            entries=entries,
            current_index=int(entry["global_index"]),
            body_font=body_font,
            current_font=body_bold_font,
            time_font=time_font,
            max_width=body_width,
            available_height=body_height,
        )

        y_cursor = body_top
        for item in item_layouts:
            box = (body_left, y_cursor, body_right, y_cursor + item["height"])
            if item["is_current"]:
                draw.rounded_rectangle(box, radius=18, fill=_ACCENT_BG)
                draw.rounded_rectangle(box, radius=18, outline=_ACCENT_SOFT, width=2)
                draw.rounded_rectangle(
                    (box[0], box[1], box[0] + 5, box[3]),
                    radius=4,
                    fill=_ACCENT,
                )
            x_time = body_left + 16
            x_text = body_left + 96
            timestamp_fill = _ACCENT if item["is_current"] else _TEXT_SECONDARY
            text_fill = _TEXT_PRIMARY
            chosen_font = body_bold_font if item["is_current"] else body_font
            draw.text((x_time, y_cursor + 14), item["timestamp"], font=time_font, fill=timestamp_fill)
            text_y = y_cursor + 12
            for line in item["lines"]:
                draw.text((x_text, text_y), line, font=chosen_font, fill=text_fill)
                text_y += item["line_height"]
            if item["slide_number"] != int(entry["slide_number"]):
                label = f"Slide {item['slide_number']}"
                label_w = self._measure_text(draw, label, small_bold_font) + 16
                label_box = (body_right - label_w - 12, y_cursor + 12, body_right - 12, y_cursor + 38)
                draw.rounded_rectangle(label_box, radius=14, fill=(241, 245, 249, 255))
                draw.text((label_box[0] + 8, label_box[1] + 5), label, font=small_bold_font, fill=_TEXT_SECONDARY)
            y_cursor += item["height"] + 10

        footer_text = f"Current line: {self._truncate_text(entry['line'], 84)}"
        draw.line((right_x1 + 22, content_y2 - 56, right_x2 - 22, content_y2 - 56), fill=(226, 232, 240, 255), width=2)
        draw.text((right_x1 + 28, content_y2 - 43), footer_text, font=small_font, fill=_TEXT_SECONDARY)

        return frame

    def _resolve_slide(self, lecture: dict[str, Any], slide_index: int) -> dict[str, Any]:
        slides = lecture.get("slides")
        if not isinstance(slides, list) or slide_index < 0 or slide_index >= len(slides):
            raise RuntimeError(f"Slide index {slide_index} is invalid for lecture export.")
        slide = slides[slide_index]
        if not isinstance(slide, dict):
            raise RuntimeError(f"Slide payload at index {slide_index} is invalid.")
        return slide

    def _render_slide_for_entry(
        self,
        job_id: str,
        slide: dict[str, Any],
        entry: dict[str, Any],
    ) -> Image.Image:
        slide_name = str(entry["slide_name"])
        slide_bytes = self.lecture_service.render_step_text_recolor_image(
            job_id=job_id,
            slide_name=slide_name,
            step_index=int(entry["step_index"]),
            strength=1.0,
        )
        if slide_bytes is not None:
            slide_image = Image.open(BytesIO(slide_bytes)).convert("RGBA")
        else:
            slide_path = self.lecture_service.get_slide_image_path(job_id, slide_name)
            if slide_path is None:
                raise RuntimeError(f"Slide image '{slide_name}' could not be resolved for export.")
            slide_image = Image.open(slide_path).convert("RGBA")

        steps = slide.get("steps")
        if not isinstance(steps, list):
            return slide_image
        step_index = int(entry["step_index"])
        if step_index < 0 or step_index >= len(steps):
            return slide_image
        step = steps[step_index]
        if not isinstance(step, dict):
            return slide_image
        return self._apply_visual_highlights(slide_image, slide, step)

    def _apply_visual_highlights(
        self,
        slide_image: Image.Image,
        slide: dict[str, Any],
        step: dict[str, Any],
    ) -> Image.Image:
        region_ids = step.get("region_ids")
        if not isinstance(region_ids, list) or not region_ids:
            return slide_image

        active_ids = self.lecture_service._resolve_active_ids(  # noqa: SLF001
            region_ids=[str(v) for v in region_ids if isinstance(v, str)],
            clusters=slide.get("clusters") if isinstance(slide.get("clusters"), list) else [],
            groups=slide.get("groups") if isinstance(slide.get("groups"), list) else [],
        )
        if not active_ids:
            return slide_image

        regions = slide.get("regions")
        if not isinstance(regions, list):
            return slide_image
        region_map = {
            region.get("id"): region
            for region in regions
            if isinstance(region, dict) and isinstance(region.get("id"), str)
        }

        visual_regions: list[dict[str, Any]] = []
        for active_id in active_ids:
            region = region_map.get(active_id)
            if not isinstance(region, dict):
                continue
            if str(region.get("kind") or "").strip().lower() != "text":
                visual_regions.append(region)

        if not visual_regions:
            return slide_image

        canvas = slide_image.copy()

        for region in visual_regions:
            self._draw_visual_underlay(canvas, slide_image, region)
            self._draw_visual_lift(canvas, slide_image, region)
        return canvas

    def _draw_visual_underlay(self, canvas: Image.Image, source: Image.Image, region: dict[str, Any]) -> None:
        shapes = self._region_shapes(region, source.size)
        if not shapes:
            return

        for shape in shapes:
            mask = Image.new("L", source.size, 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.polygon(shape, fill=255)

            region_layer = Image.new("RGBA", source.size, (0, 0, 0, 0))
            region_layer.paste(source, (0, 0), mask)
            region_layer = ImageEnhance.Brightness(region_layer).enhance(0.32)
            region_layer = ImageEnhance.Color(region_layer).enhance(0.74)
            canvas.alpha_composite(region_layer)

            tint = Image.new("RGBA", source.size, (0, 0, 0, 0))
            tint_draw = ImageDraw.Draw(tint)
            tint_draw.polygon(shape, fill=_UNDERLAY_TINT)
            canvas.alpha_composite(tint)

    def _draw_visual_lift(self, canvas: Image.Image, source: Image.Image, region: dict[str, Any]) -> None:
        shapes = self._region_shapes(region, source.size)
        if not shapes:
            return

        for shape in shapes:
            mask = Image.new("L", source.size, 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.polygon(shape, fill=255)

            shadow = Image.new("RGBA", source.size, (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow)
            shadow_draw.polygon(self._translate_points(shape, 0, 12), fill=_SHADOW)
            shadow = shadow.filter(ImageFilter.GaussianBlur(22))
            canvas.alpha_composite(shadow)

            region_layer = Image.new("RGBA", source.size, (0, 0, 0, 0))
            region_layer.paste(source, (0, 0), mask)
            region_layer = ImageEnhance.Brightness(region_layer).enhance(1.05)
            region_layer = ImageEnhance.Color(region_layer).enhance(1.04)

            lifted = Image.new("RGBA", source.size, (0, 0, 0, 0))
            lifted.paste(region_layer, (_LIFT_DX, _LIFT_DY), region_layer)
            canvas.alpha_composite(lifted)

            overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            lifted_shape = self._translate_points(shape, _LIFT_DX, _LIFT_DY)
            overlay_draw.polygon(lifted_shape, fill=_VISUAL_TINT)
            overlay_draw.line(lifted_shape + [lifted_shape[0]], fill=_VISUAL_ACCENT, width=4)
            canvas.alpha_composite(overlay)

    def _paste_slide_image(
        self,
        frame: Image.Image,
        slide_image: Image.Image,
        bounds: tuple[int, int, int, int],
    ) -> None:
        x1, y1, x2, y2 = bounds
        inset = 28
        available_w = max(1, x2 - x1 - inset * 2)
        available_h = max(1, y2 - y1 - inset * 2)
        src_w, src_h = slide_image.size
        scale = min(available_w / max(1, src_w), available_h / max(1, src_h))
        target_w = max(1, int(round(src_w * scale)))
        target_h = max(1, int(round(src_h * scale)))
        resized = slide_image.resize((target_w, target_h), _LANCZOS)
        paste_x = x1 + (x2 - x1 - target_w) // 2
        paste_y = y1 + (y2 - y1 - target_h) // 2
        frame.alpha_composite(resized, (paste_x, paste_y))

    def _build_script_window_layouts(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        entries: list[dict[str, Any]],
        current_index: int,
        body_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        current_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        time_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
        available_height: int,
    ) -> list[dict[str, Any]]:
        line_width = max(100, max_width - 120)
        line_height = self._font_line_height(body_font, 8)
        current_line_height = self._font_line_height(current_font, 8)
        item_gap = 10
        layout_cache: dict[int, dict[str, Any]] = {}

        def build_item(index: int) -> dict[str, Any]:
            if index in layout_cache:
                return layout_cache[index]
            entry = entries[index]
            is_current = index == current_index
            font = current_font if is_current else body_font
            item_line_height = current_line_height if is_current else line_height
            wrapped = self._wrap_text(draw, str(entry["line"]), font, line_width)
            height = 28 + len(wrapped) * item_line_height
            item = {
                "index": index,
                "timestamp": str(entry["timestamp_label"]),
                "slide_number": int(entry["slide_number"]),
                "lines": wrapped,
                "height": max(62, height),
                "line_height": item_line_height,
                "is_current": is_current,
            }
            layout_cache[index] = item
            return item

        selected_indices = [current_index]
        total_height = build_item(current_index)["height"]
        prev_index = current_index - 1
        next_index = current_index + 1

        while True:
            added = False
            if prev_index >= 0:
                prev_item = build_item(prev_index)
                if total_height + item_gap + prev_item["height"] <= available_height:
                    selected_indices.insert(0, prev_index)
                    total_height += item_gap + prev_item["height"]
                    prev_index -= 1
                    added = True
                else:
                    prev_index = -1
            if next_index < len(entries):
                next_item = build_item(next_index)
                if total_height + item_gap + next_item["height"] <= available_height:
                    selected_indices.append(next_index)
                    total_height += item_gap + next_item["height"]
                    next_index += 1
                    added = True
                else:
                    next_index = len(entries)
            if not added:
                break

        return [build_item(index) for index in selected_indices]

    def _write_concat_file(
        self,
        entries: list[dict[str, Any]],
        frame_paths: list[Path],
        output_path: Path,
    ) -> Path:
        lines: list[str] = []
        for entry, frame_path in zip(entries, frame_paths):
            quoted = str(frame_path).replace("'", "'\\''")
            lines.append(f"file '{quoted}'")
            lines.append(f"duration {max(0.25, float(entry['duration_ms']) / 1000.0):.6f}")
        if frame_paths:
            last_quoted = str(frame_paths[-1]).replace("'", "'\\''")
            lines.append(f"file '{last_quoted}'")
        output_path.write_text("\n".join(lines) + "\n")
        return output_path

    @staticmethod
    def _run_ffmpeg(
        *,
        ffmpeg_path: str,
        concat_path: Path,
        audio_path: Path,
        output_path: Path,
    ) -> None:
        cmd = [
            ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-i",
            str(audio_path),
            "-vf",
            "fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            detail = stderr or stdout or str(exc)
            raise RuntimeError(f"ffmpeg failed: {detail[:800]}") from exc

    def _load_font(
        self,
        size: int,
        *,
        bold: bool = False,
        mono: bool = False,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        family = "mono" if mono else ("bold" if bold else "regular")
        cache_key = (family, int(size))
        cached = self._font_cache.get(cache_key)
        if cached is not None:
            return cached

        if mono:
            candidates = _MONO_FONT_CANDIDATES
        elif bold:
            candidates = _BOLD_FONT_CANDIDATES
        else:
            candidates = _REGULAR_FONT_CANDIDATES

        for candidate in candidates:
            path = Path(candidate)
            if not path.exists():
                continue
            try:
                font = ImageFont.truetype(str(path), size=size)
                self._font_cache[cache_key] = font
                return font
            except Exception:
                continue

        fallback = ImageFont.load_default()
        self._font_cache[cache_key] = fallback
        return fallback

    @staticmethod
    def _measure_text(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return max(0, bbox[2] - bbox[0])

    @staticmethod
    def _font_line_height(
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        extra: int = 0,
    ) -> int:
        bbox = font.getbbox("Ag")
        return max(1, bbox[3] - bbox[1]) + extra

    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        words = text.split()
        if not words:
            return [""]

        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if self._measure_text(draw, candidate, font) <= max_width:
                current = candidate
                continue
            if self._measure_text(draw, current, font) > max_width:
                lines.extend(self._break_long_word(draw, current, font, max_width))
            else:
                lines.append(current)
            current = word

        if self._measure_text(draw, current, font) > max_width:
            lines.extend(self._break_long_word(draw, current, font, max_width))
        else:
            lines.append(current)
        return lines or [text]

    def _break_long_word(
        self,
        draw: ImageDraw.ImageDraw,
        word: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        chunks: list[str] = []
        current = ""
        for char in word:
            candidate = f"{current}{char}"
            if current and self._measure_text(draw, candidate, font) > max_width:
                chunks.append(current)
                current = char
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [word]

    @staticmethod
    def _translate_points(
        points: list[tuple[int, int]],
        dx: int,
        dy: int,
    ) -> list[tuple[int, int]]:
        return [(int(x + dx), int(y + dy)) for x, y in points]

    def _region_shapes(
        self,
        region: dict[str, Any],
        size: tuple[int, int],
    ) -> list[list[tuple[int, int]]]:
        width, height = size
        shapes: list[list[tuple[int, int]]] = []

        raw_polygons = region.get("polygons")
        if isinstance(raw_polygons, list):
            for polygon in raw_polygons:
                normalized = self._normalize_polygon_points(polygon, width, height)
                if normalized is not None:
                    shapes.append(normalized)

        if not shapes:
            normalized = self._normalize_polygon_points(region.get("polygon"), width, height)
            if normalized is not None:
                shapes.append(normalized)

        if shapes:
            return shapes

        bbox = region.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return []
        try:
            x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
        except Exception:
            return []
        x1 = max(0, min(width - 1, min(x1, x2)))
        x2 = max(0, min(width - 1, max(x1, x2)))
        y1 = max(0, min(height - 1, min(y1, y2)))
        y2 = max(0, min(height - 1, max(y1, y2)))
        if x2 <= x1 or y2 <= y1:
            return []
        return [[(x1, y1), (x2, y1), (x2, y2), (x1, y2)]]

    @staticmethod
    def _normalize_polygon_points(
        polygon: Any,
        width: int,
        height: int,
    ) -> list[tuple[int, int]] | None:
        if not isinstance(polygon, list) or len(polygon) < 3:
            return None
        points: list[tuple[int, int]] = []
        for raw_point in polygon:
            if not isinstance(raw_point, list) or len(raw_point) < 2:
                continue
            try:
                x = int(round(float(raw_point[0])))
                y = int(round(float(raw_point[1])))
            except Exception:
                continue
            points.append((max(0, min(width - 1, x)), max(0, min(height - 1, y))))
        return points if len(points) >= 3 else None
