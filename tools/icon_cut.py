#!/usr/bin/env python3
"""合成シート1枚から、アイコンを個別の透過PNGへ切り出す。

使い方:
    python3 tools/icon_cut.py <シート画像> [--out sim/webui/icons] [--dry]

やること:
  1. 外周から繋がった白だけを背景と見なして透過にする
     （盾や馬体の内側にある白は背景に繋がっていないので残る）
  2. 残った塊を1枚ずつ拾い、上→下・左→右の順に並べる
  3. 兵種印は128×128、コスト印は256×256の正方形へ、
     中心と直径をそろえて書き出す

Pillow が要ります:  python3 -m pip install pillow
"""
import sys, os, math
from collections import deque

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow が要ります:  python3 -m pip install pillow")

# 期待する並び（4枚の兵種印 → 10枚のコスト印）
NAMES = ["typ-shield", "typ-spear", "typ-bow", "typ-cav"] + \
        ["cost-%02d" % i for i in range(1, 11)]
SIZE = {"typ": 128, "cost": 256}

BG_LUM = 232        # これより明るければ背景候補
BG_SAT = 26         # かつ色みが薄いこと（max-min）
FEATHER_HI = 250    # 縁のぼかし: この明るさ以上はほぼ透過
FEATHER_LO = 214    # この明るさ以下は不透明のまま
MIN_AREA_RATIO = 0.0004   # 全体に対してこれ未満の塊はゴミとして捨てる
MERGE_GAP = 6       # この画素以内で近接する塊は同じアイコンとして束ねる


def lum(p):
    return (p[0] * 299 + p[1] * 587 + p[2] * 114) // 1000


def background_mask(px, w, h):
    """外周から繋がった、明るく色みの薄い画素の集合を返す。"""
    bg = bytearray(w * h)

    def bgish(i):
        r, g, b = px[i][:3]
        return lum((r, g, b)) >= BG_LUM and (max(r, g, b) - min(r, g, b)) <= BG_SAT

    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            i = y * w + x
            if not bg[i] and bgish(i):
                bg[i] = 1; q.append(i)
    for y in range(h):
        for x in (0, w - 1):
            i = y * w + x
            if not bg[i] and bgish(i):
                bg[i] = 1; q.append(i)
    while q:
        i = q.popleft()
        y, x = divmod(i, w)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if not bg[j] and bgish(j):
                    bg[j] = 1; q.append(j)
    return bg


def near_background(bg, w, h, radius=2):
    """背景に接している画素だけを印す（縁のぼかしをここだけに効かせる）。"""
    near = bytearray(bg)
    for _ in range(radius):
        grown = bytearray(near)
        for i in range(w * h):
            if near[i]:
                continue
            y, x = divmod(i, w)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and near[ny * w + nx]:
                    grown[i] = 1
                    break
        near = grown
    return near


def components(bg, w, h):
    """背景でない画素の塊を、外接矩形の一覧として返す。"""
    seen = bytearray(w * h)
    boxes = []
    for start in range(w * h):
        if bg[start] or seen[start]:
            continue
        seen[start] = 1
        q = deque([start]); area = 0
        y0, x0 = divmod(start, w)
        x1, y1 = x0, y0
        while q:
            i = q.popleft(); area += 1
            y, x = divmod(i, w)
            if x < x0: x0 = x
            if x > x1: x1 = x
            if y < y0: y0 = y
            if y > y1: y1 = y
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if not bg[j] and not seen[j]:
                        seen[j] = 1; q.append(j)
        boxes.append([x0, y0, x1, y1, area])
    return boxes


def merge(boxes, w, h):
    """近くにある塊を束ねる（線が途切れている絵のため）。"""
    keep = [b for b in boxes if b[4] >= MIN_AREA_RATIO * w * h]
    changed = True
    while changed:
        changed = False
        out = []
        for b in keep:
            for a in out:
                if (a[0] - MERGE_GAP <= b[2] and b[0] - MERGE_GAP <= a[2] and
                        a[1] - MERGE_GAP <= b[3] and b[1] - MERGE_GAP <= a[3]):
                    a[0] = min(a[0], b[0]); a[1] = min(a[1], b[1])
                    a[2] = max(a[2], b[2]); a[3] = max(a[3], b[3])
                    a[4] += b[4]; changed = True
                    break
            else:
                out.append(b)
        keep = out
    return keep


def reading_order(boxes):
    """上から下、同じ段の中では左から右へ。"""
    if not boxes:
        return boxes
    hs = sorted(b[3] - b[1] for b in boxes)
    tol = hs[len(hs) // 2] * 0.6
    rows, rest = [], sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    for b in rest:
        cy = (b[1] + b[3]) / 2
        for row in rows:
            if abs(cy - row[0]) <= tol:
                row[1].append(b)
                row[0] = sum((r[1] + r[3]) / 2 for r in row[1]) / len(row[1])
                break
        else:
            rows.append([cy, [b]])
    out = []
    for _, row in rows:
        out.extend(sorted(row, key=lambda b: b[0]))
    return out


def cut(img, box, bg, near, w, side):
    """1枚を透過つきで切り出し、正方形の側に合わせて縮める。"""
    x0, y0, x1, y1 = box[:4]
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    n = max(bw, bh)
    pad = int(n * 0.04) + 2          # 縁が切れないよう少しだけ余白
    n += pad * 2
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    tile = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    src = img.load()
    dst = tile.load()
    ox, oy = cx - n // 2, cy - n // 2
    W, H = img.size
    for ty in range(n):
        sy = oy + ty
        if not (0 <= sy < H):
            continue
        for tx in range(n):
            sx = ox + tx
            if not (0 <= sx < W):
                continue
            k = sy * w + sx
            if bg[k]:
                continue                     # 背景は抜く
            r, g, b = src[sx, sy][:3]
            if not near[k]:
                a = 255                      # 内側の白（盾や馬体）はそのまま
            else:
                L = lum((r, g, b))           # 縁だけ、白い滲みを薄める
                if L >= FEATHER_HI:
                    a = 0
                elif L > FEATHER_LO:
                    a = int(255 * (FEATHER_HI - L) / (FEATHER_HI - FEATHER_LO))
                else:
                    a = 255
            if a:
                dst[tx, ty] = (r, g, b, a)
    return tile.resize((side, side), Image.LANCZOS)


def main(argv):
    if not argv:
        sys.exit(__doc__)
    path = argv[0]
    out = "sim/webui/icons"
    dry = False
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    if "--dry" in argv:
        dry = True

    img = Image.open(path).convert("RGB")
    w, h = img.size
    print("シート: %s  %d×%d" % (os.path.basename(path), w, h))

    px = img.load()
    flat = [px[i % w, i // w] for i in range(w * h)]
    bg = background_mask(flat, w, h)
    print("背景として抜いた画素: %.1f%%" % (100.0 * sum(bg) / (w * h)))

    near = near_background(bg, w, h)
    boxes = reading_order(merge(components(bg, w, h), w, h))
    print("見つけた塊: %d 個" % len(boxes))
    for i, b in enumerate(boxes):
        print("  %2d  x %4d-%4d  y %4d-%4d  (%d×%d)"
              % (i + 1, b[0], b[2], b[1], b[3], b[2] - b[0] + 1, b[3] - b[1] + 1))
    if len(boxes) != len(NAMES):
        print("！ 期待は %d 個です。並びの見当が外れているので、"
              "上の一覧を見て切り出しの設定を直してください。" % len(NAMES))
        if not dry:
            return 1
    if dry:
        return 0

    os.makedirs(out, exist_ok=True)
    rgba = img.convert("RGB")
    for name, box in zip(NAMES, boxes):
        side = SIZE["typ" if name.startswith("typ") else "cost"]
        cut(rgba, box, bg, near, w, side).save(os.path.join(out, name + ".png"))
        print("書き出し: %s.png  %d×%d" % (name, side, side))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
