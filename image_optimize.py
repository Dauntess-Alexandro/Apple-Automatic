"""Local PNG/JPEG optimization: compression and EXIF/metadata removal."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading

from PIL import Image, ImageOps

JPEG_EXTENSIONS = {".jpg", ".jpeg"}
PNG_EXTENSIONS = {".png"}
IMAGE_EXTENSIONS = JPEG_EXTENSIONS | PNG_EXTENSIONS | {".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}

DEFAULT_JPEG_QUALITY = 85
DEFAULT_PNG_COMPRESS_LEVEL = 9


def strip_and_prepare_image(img: Image.Image) -> Image.Image:
    """Apply EXIF orientation and return an image without embedded metadata."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        clean = Image.new("RGBA", img.size)
        clean.paste(img.convert("RGBA"))
        return clean
    clean = Image.new("RGB", img.size)
    clean.paste(img.convert("RGB"))
    return clean


def save_optimized_image(
    img: Image.Image,
    dest_path: str,
    *,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    png_compress_level: int = DEFAULT_PNG_COMPRESS_LEVEL,
) -> None:
    ext = os.path.splitext(dest_path)[1].lower()
    if ext in JPEG_EXTENSIONS:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(
            dest_path,
            "JPEG",
            quality=jpeg_quality,
            optimize=True,
            progressive=True,
        )
        return
    if ext in PNG_EXTENSIONS:
        save_kwargs = {"optimize": True, "compress_level": png_compress_level}
        if img.mode == "RGBA":
            img.save(dest_path, "PNG", **save_kwargs)
            return
        img.convert("RGB").save(dest_path, "PNG", **save_kwargs)
        return
    if img.mode in ("RGBA", "LA"):
        img.save(dest_path, "PNG", optimize=True, compress_level=png_compress_level)
    else:
        img.convert("RGB").save(
            dest_path,
            "JPEG",
            quality=jpeg_quality,
            optimize=True,
            progressive=True,
        )


def optimize_image_file(
    file_path: str,
    dest_path: str | None = None,
    *,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    png_compress_level: int = DEFAULT_PNG_COMPRESS_LEVEL,
) -> str:
    """Optimize one image file. Returns path to the optimized file."""
    source_path = os.path.abspath(file_path)
    if not os.path.isfile(source_path):
        raise FileNotFoundError(source_path)

    with Image.open(source_path) as img:
        prepared = strip_and_prepare_image(img)
        if dest_path:
            output_path = os.path.abspath(dest_path)
        else:
            fd, output_path = tempfile.mkstemp(
                suffix=os.path.splitext(source_path)[1] or ".jpg",
                prefix="imgopt_",
            )
            os.close(fd)
        save_optimized_image(
            prepared,
            output_path,
            jpeg_quality=jpeg_quality,
            png_compress_level=png_compress_level,
        )
    return output_path


class LocalImageOptimizer:
    """Thread-safe local PNG/JPEG optimizer for upload pipelines."""

    def __init__(
        self,
        enabled=True,
        logger_callback=None,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        png_compress_level: int = DEFAULT_PNG_COMPRESS_LEVEL,
    ):
        self.enabled = bool(enabled)
        self.logger = logger_callback or (lambda _msg: None)
        self.jpeg_quality = jpeg_quality
        self.png_compress_level = png_compress_level
        self._cache = {}
        self._temp_files = []
        self._lock = threading.Lock()

    def compress(self, file_path):
        if not self.enabled:
            return file_path
        with self._lock:
            if file_path in self._cache:
                return self._cache[file_path]

        original_size = os.path.getsize(file_path)
        try:
            suffix = os.path.splitext(file_path)[1] or ".jpeg"
            fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="imgopt_")
            os.close(fd)
            optimize_image_file(
                file_path,
                temp_path,
                jpeg_quality=self.jpeg_quality,
                png_compress_level=self.png_compress_level,
            )
            new_size = os.path.getsize(temp_path)
            with self._lock:
                if file_path in self._cache:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    return self._cache[file_path]
                self._temp_files.append(temp_path)
                self._cache[file_path] = temp_path
            self.logger(
                f"Оптимизация: {os.path.basename(file_path)} "
                f"{original_size // 1024} KB → {new_size // 1024} KB (EXIF убран)"
            )
            return temp_path
        except Exception as exc:
            self.logger(
                f"⚠️ Оптимизация ({os.path.basename(file_path)}): {exc}. Загружаем оригинал."
            )
            with self._lock:
                self._cache[file_path] = file_path
            return file_path

    def cleanup(self):
        with self._lock:
            temp_paths = list(self._temp_files)
            self._temp_files.clear()
            self._cache.clear()
        for temp_path in temp_paths:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass


def _iter_input_files(paths: list[str]) -> list[str]:
    files = []
    for path in paths:
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                full_path = os.path.join(path, name)
                if os.path.isfile(full_path) and os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS:
                    files.append(full_path)
        elif os.path.isfile(path):
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сжать PNG/JPG и убрать EXIF/метаданные локально (без внешних API)."
    )
    parser.add_argument("paths", nargs="+", help="Файлы или папки с изображениями")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Перезаписать исходные файлы",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Папка для оптимизированных копий (по умолчанию — рядом с исходником)",
    )
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    parser.add_argument("--png-level", type=int, default=DEFAULT_PNG_COMPRESS_LEVEL)
    args = parser.parse_args(argv)

    files = _iter_input_files(args.paths)
    if not files:
        print("Нет подходящих изображений.", file=sys.stderr)
        return 1

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    for file_path in files:
        original_size = os.path.getsize(file_path)
        if args.in_place:
            temp_path = optimize_image_file(
                file_path,
                jpeg_quality=args.jpeg_quality,
                png_compress_level=args.png_level,
            )
            os.replace(temp_path, file_path)
            output_path = file_path
        elif args.output_dir:
            output_path = os.path.join(args.output_dir, os.path.basename(file_path))
            optimize_image_file(
                file_path,
                output_path,
                jpeg_quality=args.jpeg_quality,
                png_compress_level=args.png_level,
            )
        else:
            base, ext = os.path.splitext(file_path)
            output_path = f"{base}_optimized{ext}"
            optimize_image_file(
                file_path,
                output_path,
                jpeg_quality=args.jpeg_quality,
                png_compress_level=args.png_level,
            )
        new_size = os.path.getsize(output_path)
        print(
            f"{os.path.basename(file_path)}: {original_size // 1024} KB → "
            f"{new_size // 1024} KB → {output_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
