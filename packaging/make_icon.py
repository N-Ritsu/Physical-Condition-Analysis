"""デスクトップのショートカット用アイコン（assets/app.ico）を作る。

アイコンのデザインを変えたいときだけ実行する（通常のビルドでは不要。出力のapp.icoはリポジトリに含める）。
    python packaging/make_icon.py
Pillow が必要（streamlit の依存として入っている）。
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets" / "app.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
BASE = 1024  # 大きく描いてから縮小すると、小さいサイズでも線がきれいになる


def draw_icon() -> Image.Image:
    image = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 背景: 角丸の四角（上が明るく、下が少し暗い緑青）
    top, bottom = (64, 170, 160), (32, 110, 125)
    gradient = Image.new("RGBA", (BASE, BASE))
    for y in range(BASE):
        t = y / (BASE - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        ImageDraw.Draw(gradient).line([(0, y), (BASE, y)], fill=color)
    mask = Image.new("L", (BASE, BASE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, BASE - 40, BASE - 40], radius=210, fill=255)
    image.paste(gradient, (0, 0), mask)

    # 折れ線グラフ（体調の推移）と、最新の点
    points = [(170, 640), (340, 520), (480, 600), (640, 380), (780, 470), (860, 330)]
    draw.line(points, fill=(255, 255, 255, 255), width=70, joint="curve")
    for x, y in points[:1] + points[-1:]:
        draw.ellipse([x - 35, y - 35, x + 35, y + 35], fill=(255, 255, 255, 255))
    x, y = points[-1]
    draw.ellipse([x - 62, y - 62, x + 62, y + 62], fill=(255, 214, 102, 255))

    # 土台の線
    draw.rounded_rectangle([170, 760, 860, 800], radius=20, fill=(255, 255, 255, 150))
    return image


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw_icon().save(OUT, format="ICO", sizes=SIZES)
    print(f"作成: {OUT}（{OUT.stat().st_size} バイト）")


if __name__ == "__main__":
    main()
