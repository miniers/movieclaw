"""库封面拼贴——服务端渲染的「氛围光货架」（双端共用）。

复刻控制台 LibraryCover 组件（apps/web/components/library-view.tsx）的构图，
让 Jellyfin 兼容层（播放器库卡片）与控制台媒体库页显示**同一张**真实图片：

- 画布 21:10；氛围光 = 首张海报放大重模糊提饱和铺满，再压暗保证前景对比；
- 至多 4 张海报（各占画布宽 22.5%，2:3 竖版，圆角 + 白描边 + 落影）立排；
- 每张海报下方带向下渐隐的倒影；底部一枚中性地面光斑像射灯打在舞台上。

素材选择：库内**最近入库**且有本地海报资产的 4 部作品（与控制台货架同一
口径）。产物落 data/metadata/library-covers/{库id}-{key}.jpg，key 由海报
路径+mtime 派生——库内容变化自动重渲，旧文件顺手清理。渲染是 CPU 活，
统一走 asyncio.to_thread，不堵事件循环。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from sqlalchemy import func, select

from movieclaw_api.core.config import get_settings
from movieclaw_db.engine import get_database
from movieclaw_db.models import LibraryFile, MediaMetadata

logger = logging.getLogger("movieclaw_api.library_cover")

# 画布：控制台卡片是 21/10 比例；1260x600 对 2x 屏的卡片宽度绰绰有余
CANVAS_W, CANVAS_H = 1260, 600
POSTER_W_RATIO = 0.225  # 单张海报占画布宽
POSTER_GAP_RATIO = 0.02
POSTER_TOP_RATIO = 0.045
MAX_POSTERS = 4


def covers_dir() -> Path:
    return Path(get_settings().metadata_dir) / "library-covers"


def _assets_root() -> Path:
    from movieclaw_api.services.media_scrape import assets_root

    return Path(assets_root())


async def select_cover_posters(library_id: int) -> list[Path]:
    """选出该库最近入库、有本地海报资产的至多 4 部作品的海报绝对路径。"""
    root = _assets_root()
    async with get_database().session() as session:
        # 只需要海报路径与入库时间：整行读取会反序列化每部作品的简介、演员等
        # 大字段；VidHub 的根级 Items 每次启动都会走这里，大库上代价不可接受。
        rows = (
            await session.execute(
                select(
                    MediaMetadata.poster_file,
                    func.max(LibraryFile.created_at).label("latest_created_at"),
                )
                .join(
                    LibraryFile,
                    LibraryFile.media_item_id == MediaMetadata.media_item_id,
                )
                .where(
                    LibraryFile.library_id == library_id,
                    LibraryFile.missing_since.is_(None),
                    MediaMetadata.poster_file.is_not(None),
                )
                .group_by(MediaMetadata.media_item_id, MediaMetadata.poster_file)
                .order_by(func.max(LibraryFile.created_at).desc())
            )
        ).all()
    result: list[Path] = []
    for rel, _created_at in rows:
        path = root / rel
        if path.is_file():
            result.append(path)
        if len(result) >= MAX_POSTERS:
            break
    return result


def _cover_key(paths: list[Path]) -> str:
    hasher = hashlib.md5()
    for p in paths:
        try:
            hasher.update(f"{p}:{p.stat().st_mtime_ns};".encode())
        except OSError:
            hasher.update(f"{p}:gone;".encode())
    return hasher.hexdigest()


async def ensure_library_cover(library_id: int) -> tuple[Path, str] | None:
    """确保拼贴封面已渲染并返回 (文件路径, 版本 key)；无素材返回 None。

    幂等：key 命中直接返回；素材变化重渲并清理该库旧产物。
    """
    posters = await select_cover_posters(library_id)
    if not posters:
        return None
    key = _cover_key(posters)
    out_dir = covers_dir()
    target = out_dir / f"{library_id}-{key}.jpg"
    if target.is_file():
        return target, key
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(render_shelf_collage, posters, target)
    except Exception:
        logger.exception("库封面拼贴渲染失败（library_id=%d），本次退化为无封面", library_id)
        return None
    for stale in out_dir.glob(f"{library_id}-*.jpg"):
        if stale != target:
            stale.unlink(missing_ok=True)
    return target, key


def render_shelf_collage(poster_paths: list[Path], out: Path) -> None:
    """Pillow 渲染「氛围光货架」。纯同步，调用方负责丢线程池。"""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    def load_poster(path: Path, width: int) -> Image.Image:
        img = Image.open(path).convert("RGB")
        height = round(width * 3 / 2)
        # cover 语义裁剪到 2:3
        scale = max(width / img.width, height / img.height)
        img = img.resize((round(img.width * scale), round(img.height * scale)))
        left = (img.width - width) // 2
        top = (img.height - height) // 2
        return img.crop((left, top, left + width, top + height))

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), "#080a10")

    # ---- 氛围光底：首图 cover 铺满 → 放大 1.5x → 重模糊 → 提饱和 → 压暗 ----
    first = Image.open(poster_paths[0]).convert("RGB")
    scale = max(CANVAS_W / first.width, CANVAS_H / first.height) * 1.5
    ambient = first.resize((round(first.width * scale), round(first.height * scale)))
    left = (ambient.width - CANVAS_W) // 2
    top = (ambient.height - CANVAS_H) // 2
    ambient = ambient.crop((left, top, left + CANVAS_W, top + CANVAS_H))
    ambient = ambient.filter(ImageFilter.GaussianBlur(56))
    ambient = ImageEnhance.Color(ambient).enhance(1.5)
    canvas = Image.blend(canvas, ambient, 0.7)  # 首图 opacity 0.7 落在深底上
    dark = Image.new("RGB", canvas.size, "#080a10")
    canvas = Image.blend(canvas, dark, 0.5)  # bg-[#080a10]/50 压暗

    # ---- 灯箱底光：首图模糊自底向上 screen 发光（颜色天然取自海报主色）----
    glow_h = CANVAS_H // 2
    glow = ambient.resize((CANVAS_W, glow_h)).filter(ImageFilter.GaussianBlur(40))
    glow = ImageEnhance.Color(glow).enhance(1.4)
    from PIL import ImageChops

    region = canvas.crop((0, CANVAS_H - glow_h, CANVAS_W, CANVAS_H))
    screened = ImageChops.screen(region, glow)
    # 自底向上的线性渐隐（底边最亮 0.55 → 顶部 0）
    mask = Image.new("L", (CANVAS_W, glow_h))
    mask_draw = ImageDraw.Draw(mask)
    for y in range(glow_h):
        mask_draw.line(
            [(0, y), (CANVAS_W, y)], fill=round(255 * 0.55 * (y / glow_h))
        )
    region.paste(screened, (0, 0), mask)
    canvas.paste(region, (0, CANVAS_H - glow_h))

    # ---- 中性地面光斑：射灯打在舞台地面上 ----
    spot = Image.new("L", (CANVAS_W, CANVAS_H), 0)
    spot_draw = ImageDraw.Draw(spot)
    spot_draw.ellipse(
        (
            round(CANVAS_W * 0.2),
            round(CANVAS_H * 0.78),
            round(CANVAS_W * 0.8),
            round(CANVAS_H * 1.3),
        ),
        fill=round(255 * 0.09),
    )
    spot = spot.filter(ImageFilter.GaussianBlur(50))
    white = Image.new("RGB", canvas.size, "white")
    canvas.paste(white, (0, 0), spot)

    # ---- 海报排：圆角 + 白描边 + 落影 + 倒影 ----
    count = min(len(poster_paths), MAX_POSTERS)
    poster_w = round(CANVAS_W * POSTER_W_RATIO)
    poster_h = round(poster_w * 3 / 2)
    gap = round(CANVAS_W * POSTER_GAP_RATIO)
    row_w = count * poster_w + (count - 1) * gap
    x = (CANVAS_W - row_w) // 2
    y = round(CANVAS_H * POSTER_TOP_RATIO)
    radius = 6

    rounded_mask = Image.new("L", (poster_w, poster_h), 0)
    ImageDraw.Draw(rounded_mask).rounded_rectangle(
        (0, 0, poster_w - 1, poster_h - 1), radius=radius, fill=255
    )

    canvas_rgba = canvas.convert("RGBA")
    for i in range(count):
        poster = load_poster(poster_paths[i], poster_w)
        px = x + i * (poster_w + gap)

        # 落影：黑色圆角矩形下移 6px、重模糊
        shadow = Image.new("RGBA", canvas_rgba.size, (0, 0, 0, 0))
        shadow_tile = Image.new("RGBA", (poster_w, poster_h), (0, 0, 0, 128))
        shadow.paste(shadow_tile, (px, y + 8), rounded_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        canvas_rgba = Image.alpha_composite(canvas_rgba, shadow)

        # 海报本体（圆角）+ 1px 白描边
        tile = Image.new("RGBA", canvas_rgba.size, (0, 0, 0, 0))
        tile.paste(poster, (px, y), rounded_mask)
        ImageDraw.Draw(tile).rounded_rectangle(
            (px, y, px + poster_w - 1, y + poster_h - 1),
            radius=radius,
            outline=(255, 255, 255, 51),
            width=1,
        )
        canvas_rgba = Image.alpha_composite(canvas_rgba, tile)

        # 倒影：翻转副本贴底边，轻模糊，向下快速渐隐（近处最实 0.4 → 26% 处消失）
        flipped = poster.transpose(Image.FLIP_TOP_BOTTOM).filter(
            ImageFilter.GaussianBlur(1)
        )
        refl_h = poster_h
        fade = Image.new("L", (poster_w, refl_h), 0)
        fade_draw = ImageDraw.Draw(fade)
        fade_span = round(refl_h * 0.26)
        for ry in range(fade_span):
            alpha = round(255 * 0.4 * (1 - ry / fade_span))
            fade_draw.line([(0, ry), (poster_w, ry)], fill=alpha)
        refl_mask = Image.new("L", (poster_w, refl_h), 0)
        refl_mask.paste(fade, (0, 0), rounded_mask)
        refl = Image.new("RGBA", canvas_rgba.size, (0, 0, 0, 0))
        refl.paste(flipped, (px, y + poster_h + 2), refl_mask)
        canvas_rgba = Image.alpha_composite(canvas_rgba, refl)

    canvas_rgba.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
